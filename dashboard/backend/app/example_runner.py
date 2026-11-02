from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from .control_state import ActivityGate
from .examples import (
    ExampleDefinition,
    ExampleDependencyStatus,
    ExampleId,
    ExamplePublicStatus,
    load_example_registry,
)
from .persistence import ExampleLifecycleStore, MetadataStore

_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
_ACTIVE_RUNTIME_STATES = {"created", "running", "restarting", "paused"}
_ERROR_CATEGORIES = {
    "docker_unavailable",
    "build_timeout",
    "build_failed",
    "start_timeout",
    "start_failed",
    "unhealthy",
    "stop_failed",
    "recovery_required",
}
_UNSET = object()
_LOGGER = logging.getLogger(__name__)


class ExampleRunnerError(RuntimeError):
    """Sanitized, categorized example lifecycle failure."""


class ExampleDependencyUnavailable(ExampleRunnerError):
    pass


class ExampleActivityConflict(ExampleRunnerError):
    pass


class ExampleDockerUnavailable(ExampleRunnerError):
    pass


class ExampleComposeError(ExampleRunnerError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category if category in _ERROR_CATEGORIES else "start_failed"


class DockerComposeAdapter:
    """The only mutating Docker adapter for the registered chat-voice service."""

    def __init__(
        self,
        example: ExampleDefinition,
        *,
        docker_path: str | None = None,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.example = example
        self.docker_path = docker_path if docker_path is not None else shutil.which("docker")
        self._run = run

    @staticmethod
    def _controlled_environment(upstream_token: str | None = None) -> dict[str, str]:
        blocked = {
            "COMPOSE_FILE",
            "COMPOSE_PROJECT_NAME",
            "COMPOSE_PROFILES",
            "COMPOSE_PATH_SEPARATOR",
            "COMPOSE_IGNORE_ORPHANS",
            "COMPOSE_REMOVE_ORPHANS",
            "DOCKER_CONTEXT",
            "DOCKER_HOST",
        }
        environment = {key: value for key, value in os.environ.items() if key.upper() not in blocked}
        if upstream_token is not None:
            environment["EXAMPLE_UPSTREAM_TOKEN"] = upstream_token
        return environment

    def _command(
        self,
        arguments: list[str],
        *,
        timeout: float,
        upstream_token: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if not self.docker_path:
            raise ExampleDockerUnavailable("Docker is unavailable") from None
        try:
            result = self._run(
                [self.docker_path, *arguments],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                env=self._controlled_environment(upstream_token),
            )
        except FileNotFoundError:
            raise ExampleDockerUnavailable("Docker is unavailable") from None
        except subprocess.TimeoutExpired:
            raise ExampleComposeError("start_timeout") from None
        except OSError:
            raise ExampleDockerUnavailable("Docker is unavailable") from None
        return result

    def _compose(self, *arguments: str, timeout: float, upstream_token: str | None = None) -> str:
        result = self._command(
            [
                "compose",
                "--file",
                str(self.example.compose_file),
                "--project-name",
                self.example.compose_project,
                *arguments,
            ],
            timeout=timeout,
            upstream_token=upstream_token,
        )
        if result.returncode != 0:
            raise ExampleComposeError("start_failed")
        return result.stdout or ""

    def ensure_available(self) -> None:
        result = self._command(["info", "--format", "{{.ServerVersion}}"], timeout=5)
        if result.returncode != 0 or not (result.stdout or "").strip():
            raise ExampleDockerUnavailable("Docker is unavailable") from None

    def image_exists(self) -> bool:
        result = self._command(
            ["image", "ls", "--quiet", "local-ai-api-lite-chat-voice:latest"],
            timeout=5,
        )
        if result.returncode != 0:
            raise ExampleComposeError("build_failed")
        return bool((result.stdout or "").strip())

    def build(self, timeout: float) -> None:
        try:
            self._compose("build", "--pull", self.example.compose_service, timeout=timeout)
        except ExampleComposeError as exc:
            category = "build_timeout" if exc.category == "start_timeout" else "build_failed"
            raise ExampleComposeError(category) from None

    def start(self, token: str, timeout: float) -> None:
        self._compose(
            "up",
            "--detach",
            "--no-deps",
            "--no-build",
            "--pull",
            "never",
            self.example.compose_service,
            timeout=timeout,
            upstream_token=token,
        )

    def inspect(self) -> dict[str, str]:
        output = self._compose(
            "ps",
            "--all",
            "--format",
            "json",
            self.example.compose_service,
            timeout=5,
        )
        rows: list[Any] = []
        if output.strip():
            try:
                parsed = json.loads(output)
                rows = parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                try:
                    rows = [json.loads(line) for line in output.splitlines() if line.strip()]
                except json.JSONDecodeError:
                    return {"state": "unknown", "health": "unavailable"}
        if not rows:
            return {"state": "absent", "health": "unavailable"}
        if len(rows) != 1 or not isinstance(rows[0], dict):
            return {"state": "unknown", "health": "unavailable"}
        row = rows[0]
        service = row.get("Service", row.get("service"))
        if service is not None and service != self.example.compose_service:
            return {"state": "unknown", "health": "unavailable"}
        state = row.get("State", row.get("state"))
        health = row.get("Health", row.get("health"))
        if not isinstance(state, str) or state.lower() not in _ACTIVE_RUNTIME_STATES | {"exited", "dead"}:
            return {"state": "unknown", "health": "unavailable"}
        return {
            "state": state.lower(),
            "health": health.lower() if isinstance(health, str) else "unavailable",
        }

    def stop(self, timeout: float) -> None:
        self._compose(
            "stop",
            "--timeout",
            "10",
            self.example.compose_service,
            timeout=timeout,
        )


class DashboardExampleDependencyReader:
    """Read narrator status from the dashboard cache and speech status from the private Docker network."""

    def __init__(
        self,
        status_cache: Any,
        *,
        docker_path: str | None = None,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.status_cache = status_cache
        self.docker_path = docker_path if docker_path is not None else shutil.which("docker")
        self._run = run

    def read(self) -> tuple[ExampleDependencyStatus, ExampleDependencyStatus]:
        return self._read_chat(), self._read_speech()

    def _read_chat(self) -> ExampleDependencyStatus:
        try:
            snapshot = self.status_cache.get_snapshot()
        except Exception:  # noqa: BLE001 - dependency failures never leak adapter details.
            return ExampleDependencyStatus(state="unknown")
        configuration = snapshot.get("configuration") if isinstance(snapshot, dict) else None
        state = snapshot.get("inference_state") if isinstance(snapshot, dict) else None
        available = (
            snapshot.get("availability") == "available" and not snapshot.get("stale", True)
            if isinstance(snapshot, dict)
            else False
        )
        alias = configuration.get("model_alias") if isinstance(configuration, dict) else None
        active_profile = snapshot.get("active_profile") if isinstance(snapshot, dict) else None
        return ExampleDependencyStatus(
            state="ready" if available and state == "Ready" and alias == "ddu-narrator" else "unavailable",
            active_profile=active_profile if isinstance(active_profile, str) else None,
            phase=state.lower() if isinstance(state, str) else None,
            observed_at=snapshot.get("observed_at") if isinstance(snapshot.get("observed_at"), str) else None,
        )

    def _read_speech(self) -> ExampleDependencyStatus:
        if not self.docker_path:
            return ExampleDependencyStatus(state="unavailable")
        environment = DockerComposeAdapter._controlled_environment()
        try:
            listing = self._run(
                [
                    self.docker_path,
                    "ps",
                    "--all",
                    "--filter",
                    "network=ddu_inference_bus",
                    "--format",
                    "{{.ID}}",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=3,
                env=environment,
            )
            if listing.returncode != 0:
                return ExampleDependencyStatus(state="unavailable")
            ids = [
                line.strip()
                for line in (listing.stdout or "").splitlines()
                if _CONTAINER_ID.fullmatch(line.strip())
            ][:64]
            if not ids:
                return ExampleDependencyStatus(state="unavailable")
            template = (
                "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}"
                "{{else}}unknown{{end}}|{{range $name, $network := .NetworkSettings.Networks}}"
                "{{$name}}:{{range $network.Aliases}}{{.}},{{end}};{{end}}"
            )
            inspected = self._run(
                [self.docker_path, "inspect", "--format", template, *ids],
                capture_output=True,
                text=True,
                check=False,
                timeout=4,
                env=environment,
            )
            if inspected.returncode != 0:
                return ExampleDependencyStatus(state="unavailable")
            ready: set[str] = set()
            for line in (inspected.stdout or "").splitlines():
                parts = line.split("|", 2)
                if len(parts) != 3 or parts[0] != "running" or parts[1] != "healthy":
                    continue
                for network in parts[2].split(";"):
                    if not network.startswith("ddu_inference_bus:"):
                        continue
                    aliases = set(network.partition(":")[2].split(","))
                    ready.update(aliases & {"ddu-stt", "ddu-tts"})
            return ExampleDependencyStatus(
                state="ready" if ready == {"ddu-stt", "ddu-tts"} else "unavailable",
            )
        except (OSError, subprocess.SubprocessError):
            return ExampleDependencyStatus(state="unavailable")


class ExampleRunner:
    """Own the single registered chat-voice container and its shared activity reservation."""

    gate_operation_id = "example:chat-voice"

    def __init__(
        self,
        *,
        metadata: MetadataStore,
        gate: ActivityGate,
        dependency_reader: Any,
        compose: Any | None = None,
        registry_loader: Callable[[], dict[ExampleId, ExampleDefinition]] = load_example_registry,
        clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        token_factory: Callable[[], str] | None = None,
        background: bool = True,
        build_timeout: float = 600,
        start_timeout: float = 90,
        stop_timeout: float = 20,
        poll_interval: float = 1,
    ) -> None:
        if min(build_timeout, start_timeout, stop_timeout, poll_interval) <= 0:
            raise ValueError("Example lifecycle deadlines must be positive")
        self.lifecycle = ExampleLifecycleStore(metadata)
        self.gate = gate
        self.dependency_reader = dependency_reader
        self.registry_loader = registry_loader
        self.clock = clock
        self.monotonic_clock = monotonic_clock
        self.sleeper = sleeper
        self.token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self.background = background
        self.build_timeout = build_timeout
        self.start_timeout = start_timeout
        self.stop_timeout = stop_timeout
        self.poll_interval = poll_interval
        self._lock = threading.RLock()
        self._container_lock = threading.Lock()
        self._token: str | None = None
        self._start_thread: threading.Thread | None = None
        self._stop_requested = threading.Event()
        self.compose = compose
        self._idle_thread: threading.Thread | None = None

    def _example(self, example_id: str) -> ExampleDefinition:
        if example_id != "chat-voice":
            raise ValueError("The requested example ID is not supported")
        examples = self.registry_loader()
        return examples["chat-voice"]

    def _adapter(self) -> Any:
        if self.compose is not None:
            return self.compose
        return DockerComposeAdapter(self._example("chat-voice"))

    def _dependencies(self) -> tuple[ExampleDependencyStatus, ExampleDependencyStatus]:
        try:
            chat, voice = self.dependency_reader.read()
            return ExampleDependencyStatus.model_validate(chat), ExampleDependencyStatus.model_validate(voice)
        except Exception:  # noqa: BLE001 - dependency errors stay content-free and fail closed.
            return ExampleDependencyStatus(state="unknown"), ExampleDependencyStatus(state="unknown")

    def _now(self) -> str:
        return datetime.fromtimestamp(self.clock(), UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _save(
        self,
        state: str,
        *,
        started_at: str | None | object = _UNSET,
        error: str | None = None,
        idle_expires_at: str | None | object = _UNSET,
    ) -> dict[str, str | None]:
        current = self.lifecycle.get()
        if started_at is _UNSET:
            started_at = current["started_at"]
        if idle_expires_at is _UNSET:
            idle_expires_at = current["idle_expires_at"]
        if not isinstance(started_at, str) and started_at is not None:
            raise ValueError("Example lifecycle start time is invalid")
        if not isinstance(idle_expires_at, str) and idle_expires_at is not None:
            raise ValueError("Example lifecycle idle time is invalid")
        return self.lifecycle.save(
            state=state,
            started_at=started_at,
            last_error_category=error,
            idle_expires_at=idle_expires_at,
            updated_at=self._now(),
        )

    def _public(self, record: dict[str, str | None] | None = None) -> ExamplePublicStatus:
        example = self._example("chat-voice")
        record = record or self.lifecycle.get()
        chat, voice = self._dependencies()
        try:
            current_gate = self.gate.current()
            activity_available = current_gate is None or current_gate.get("operation_id") == self.gate_operation_id
        except Exception:  # noqa: BLE001 - gate errors fail closed for launch controls.
            activity_available = False
        missing = []
        if chat.state != "ready":
            missing.append(example.chat_model_alias)
        if voice.state != "ready":
            missing.extend(example.speech_services)
        return ExamplePublicStatus(
            id="chat-voice",
            name=example.name,
            state=record["state"],
            chat_dependency=chat,
            voice_dependency=voice,
            missing_dependencies=missing,
            started_at=record["started_at"],
            last_error_category=record["last_error_category"],
            idle_expires_at=record["idle_expires_at"],
            activity_available=activity_available,
        )

    def list_status(self) -> list[ExamplePublicStatus]:
        self._example("chat-voice")
        current = self.lifecycle.get()
        if current["state"] in {"ready", "recovery-required", "stopping"} or (
            current["state"] == "starting"
            and (self._start_thread is None or not self._start_thread.is_alive())
        ):
            return self.reconcile()
        return [self._public()]

    def touch_activity(self) -> ExamplePublicStatus:
        """Extend the fixed idle deadline after a dashboard-proxied app request."""
        example = self._example("chat-voice")
        with self._lock:
            current = self.lifecycle.get()
            if current["state"] != "ready" or self._token is None:
                raise ExampleActivityConflict("The example is not ready for requests") from None
            expires = datetime.fromtimestamp(self.clock(), UTC) + timedelta(
                seconds=example.idle_timeout_seconds
            )
            expiry_text = expires.isoformat(timespec="seconds").replace("+00:00", "Z")
            record = self._save(
                "ready",
                started_at=current["started_at"],
                idle_expires_at=expiry_text,
            )
        return self._public(record)

    def get_upstream_token(self) -> str:
        """Return the controller-owned token only to its in-process proxy."""
        with self._lock:
            current = self.lifecycle.get()
            if current["state"] != "ready" or not self._token:
                raise ExampleActivityConflict("The example is not ready for requests") from None
            return self._token

    def check_idle_timeout(self) -> ExamplePublicStatus:
        current = self.lifecycle.get()
        if current["state"] != "ready" or not current["idle_expires_at"]:
            return self._public(current)
        try:
            expires = datetime.fromisoformat(current["idle_expires_at"])
        except ValueError:
            with self._lock:
                record = self._save(
                    "recovery-required",
                    error="recovery_required",
                    started_at=current["started_at"],
                )
            return self._public(record)
        if self.clock() >= expires.timestamp():
            return self.stop("chat-voice")
        return self._public(current)

    def _idle_monitor(self) -> None:
        while True:
            self.sleeper(5)
            try:
                self.check_idle_timeout()
            except Exception as exc:  # noqa: BLE001 - monitor errors stay local and retry on the next pass.
                _LOGGER.warning("Example idle check failed (%s)", type(exc).__name__)
                continue

    def start_idle_monitor(self) -> None:
        if not self.background or (self._idle_thread is not None and self._idle_thread.is_alive()):
            return
        self._idle_thread = threading.Thread(
            target=self._idle_monitor,
            name="chat-voice-example-idle-monitor",
            daemon=True,
        )
        self._idle_thread.start()

    def start(self, example_id: ExampleId) -> ExamplePublicStatus:
        example = self._example(example_id)
        with self._lock:
            current = self.lifecycle.get()
            if current["state"] in {"starting", "ready", "stopping", "recovery-required"}:
                if current["state"] == "starting":
                    return self._public(current)
                if current["state"] == "ready" and self._token is not None:
                    return self._public(current)
                raise ExampleActivityConflict("The example requires recovery before another start")

            chat, _voice = self._dependencies()
            if chat.state != "ready":
                raise ExampleDependencyUnavailable("The ddu-narrator dependency is unavailable") from None
            adapter = self._adapter()
            if not self.gate.reserve("example", self.gate_operation_id, self.clock()):
                raise ExampleActivityConflict("Another dashboard activity owns the operation gate") from None

            try:
                adapter.ensure_available()
            except ExampleDockerUnavailable:
                self._save("recovery-required", error="docker_unavailable")
                raise

            try:
                runtime = adapter.inspect()
            except Exception:  # noqa: BLE001 - uncertain container state must remain gated.
                self._save("recovery-required", error="docker_unavailable")
                raise ExampleDockerUnavailable("Docker is unavailable") from None
            if runtime.get("state") not in {"absent", "exited", "dead"}:
                self._save("recovery-required", error="recovery_required")
                raise ExampleActivityConflict("The existing example container requires recovery") from None

            self._stop_requested.clear()
            token = self.token_factory()
            self._token = token
            started_at = self._now()
            expires = datetime.fromtimestamp(self.clock(), UTC) + timedelta(
                seconds=example.idle_timeout_seconds
            )
            expiry_text = expires.isoformat(timespec="seconds").replace("+00:00", "Z")
            record = self._save(
                "starting",
                started_at=started_at,
                error=None,
                idle_expires_at=expiry_text,
            )
            if self.background:
                thread = threading.Thread(
                    target=self._start_worker,
                    args=(adapter, token),
                    name="chat-voice-example-start",
                    daemon=True,
                )
                self._start_thread = thread
                thread.start()
            else:
                self._start_worker(adapter, token)
                record = self.lifecycle.get()
            return self._public(record)

    def _start_worker(self, adapter: Any, token: str) -> None:
        try:
            if not adapter.image_exists():
                adapter.build(self.build_timeout)
            if self._stop_requested.is_set():
                self._finish_cancelled_start(adapter)
                return
            with self._container_lock:
                if self._stop_requested.is_set():
                    self._finish_cancelled_start(adapter)
                    return
                adapter.start(token, self.start_timeout)
            deadline = self.monotonic_clock() + self.start_timeout
            while True:
                if self._stop_requested.is_set():
                    self._finish_cancelled_start(adapter)
                    return
                runtime = adapter.inspect()
                if self._stop_requested.is_set():
                    self._finish_cancelled_start(adapter)
                    return
                state = runtime.get("state")
                health = runtime.get("health")
                if state == "running" and health == "healthy":
                    with self._lock:
                        if not self._stop_requested.is_set():
                            current = self.lifecycle.get()
                            self._save("ready", started_at=current["started_at"])
                    return
                if state == "running" and health == "unhealthy":
                    self._fail_start(adapter, "unhealthy")
                    return
                if state in {"exited", "dead"}:
                    self._fail_start(adapter, "start_failed")
                    return
                remaining = deadline - self.monotonic_clock()
                if remaining <= 0:
                    self._fail_start(adapter, "start_timeout")
                    return
                self.sleeper(min(self.poll_interval, remaining))
        except ExampleDockerUnavailable:
            if self._stop_requested.is_set():
                self._finish_cancelled_start(adapter)
            else:
                self._fail_start(adapter, "docker_unavailable")
        except ExampleComposeError as exc:
            if self._stop_requested.is_set():
                self._finish_cancelled_start(adapter)
            else:
                self._fail_start(adapter, exc.category)
        except subprocess.TimeoutExpired:
            if self._stop_requested.is_set():
                self._finish_cancelled_start(adapter)
            else:
                self._fail_start(adapter, "start_timeout")
        except Exception:  # noqa: BLE001 - command details and request content remain private.
            if self._stop_requested.is_set():
                self._finish_cancelled_start(adapter)
            else:
                self._fail_start(adapter, "start_failed")

    def _finish_cancelled_start(self, adapter: Any) -> None:
        """Stop any container a cancelled worker may have created before releasing its lease."""
        with self._container_lock:
            try:
                runtime = adapter.inspect()
                if runtime.get("state") in _ACTIVE_RUNTIME_STATES:
                    adapter.stop(self.stop_timeout)
                    runtime = adapter.inspect()
            except Exception:  # noqa: BLE001 - unknown runtime must keep the gate reserved.
                runtime = {"state": "unknown"}
            self._confirm_stopped(adapter, error="stop_failed" if runtime.get("state") == "unknown" else None, runtime=runtime)

    def _fail_start(self, adapter: Any, category: str) -> None:
        if self._stop_requested.is_set():
            return
        try:
            runtime = adapter.inspect()
        except Exception:  # noqa: BLE001 - unknown runtime remains gated.
            runtime = {"state": "unknown"}
        state = runtime.get("state")
        if state in _ACTIVE_RUNTIME_STATES:
            if category == "unhealthy":
                try:
                    with self._container_lock:
                        adapter.stop(self.stop_timeout)
                        runtime = adapter.inspect()
                    state = runtime.get("state")
                except Exception:  # noqa: BLE001 - keep the gate if stop cannot be confirmed.
                    state = "unknown"
            if state not in {"absent", "exited", "dead"}:
                with self._lock:
                    current = self.lifecycle.get()
                    self._save(
                        "recovery-required",
                        started_at=current["started_at"],
                        error=category if category in _ERROR_CATEGORIES else "start_failed",
                    )
                return
        self._token = None
        with self._lock:
            current = self.lifecycle.get()
            self._save(
                "failed",
                started_at=current["started_at"],
                error=category if category in _ERROR_CATEGORIES else "start_failed",
                idle_expires_at=None,
            )
            self.gate.release(self.gate_operation_id)

    def _confirm_stopped(
        self,
        adapter: Any,
        error: str | None,
        runtime: dict[str, str] | None = None,
    ) -> bool:
        if runtime is None:
            try:
                runtime = adapter.inspect()
            except Exception:  # noqa: BLE001 - unknown Docker state is not proof of exit.
                runtime = {"state": "unknown"}
        if runtime.get("state") not in {"absent", "exited", "dead"}:
            with self._lock:
                current = self.lifecycle.get()
                self._save(
                    "recovery-required",
                    started_at=current["started_at"],
                    error=error or "stop_failed",
                )
            return False
        self._token = None
        with self._lock:
            self._save("stopped", started_at=None, error=error, idle_expires_at=None)
            self.gate.release(self.gate_operation_id)
        return True

    def stop(self, example_id: ExampleId) -> ExamplePublicStatus:
        self._example(example_id)
        adapter = self._adapter()
        with self._lock:
            current = self.lifecycle.get()
            if current["state"] == "stopped":
                try:
                    runtime = adapter.inspect()
                except Exception:  # noqa: BLE001 - unknown state needs recovery.
                    runtime = {"state": "unknown"}
                if runtime.get("state") in {"absent", "exited", "dead"}:
                    return self._public(current)
            if not self.gate.reserve("example", self.gate_operation_id, self.clock()):
                raise ExampleActivityConflict("Another dashboard activity owns the operation gate") from None
            self._stop_requested.set()
            self._save("stopping", started_at=current["started_at"])

        with self._container_lock:
            try:
                adapter.ensure_available()
                runtime = adapter.inspect()
                if runtime.get("state") in {"absent", "exited", "dead"}:
                    if self._start_thread is not None and self._start_thread.is_alive():
                        # The worker may still be building and must get its cancellation check
                        # before a stopped state can release the shared activity gate.
                        return self._public()
                    self._confirm_stopped(adapter, error=None, runtime=runtime)
                else:
                    adapter.stop(self.stop_timeout)
                    self._confirm_stopped(adapter, error=None)
            except Exception:  # noqa: BLE001 - preserve fixed, sanitized stop categories.
                self._confirm_stopped(adapter, error="stop_failed")
        return self._public()

    def reconcile(self) -> list[ExamplePublicStatus]:
        self._example("chat-voice")
        adapter = self._adapter()
        current = self.lifecycle.get()
        if self._start_thread is not None and self._start_thread.is_alive():
            return [self._public(current)]
        try:
            runtime = adapter.inspect()
        except Exception:  # noqa: BLE001 - unknown Docker state remains gated.
            runtime = {"state": "unknown", "health": "unavailable"}
            error = "docker_unavailable"
        else:
            error = None
        state = runtime.get("state")
        if state in {"absent", "exited", "dead"}:
            self._stop_requested.clear()
            self._confirm_stopped(adapter, error=None, runtime=runtime)
            return [self._public()]
        try:
            reserved = self.gate.reserve("example", self.gate_operation_id, self.clock())
        except Exception:  # noqa: BLE001 - failures retain recovery-required status.
            reserved = False
        if state in _ACTIVE_RUNTIME_STATES and reserved:
            next_state = "recovery-required" if self._token is None else (
                "ready" if runtime.get("health") == "healthy" else "starting"
            )
            self._save(next_state, started_at=current["started_at"], error=error)
        else:
            self._save(
                "recovery-required",
                started_at=current["started_at"],
                error=error or "recovery_required",
            )
        return [self._public()]
