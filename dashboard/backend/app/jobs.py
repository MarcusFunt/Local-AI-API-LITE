from __future__ import annotations

import json
import logging
import math
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

from .configuration import load_allowlisted_profile
from .paths import RuntimePathError, data_directory, ensure_private_directory, ensure_private_file
from .persistence import JOB_SCHEMA, MetadataStore
from .telemetry import read_host_telemetry

MAX_OUTPUT_BYTES = 64 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024
MAX_ARTIFACT_LINES = 500
MAX_ARTIFACT_FILES = 30
MAX_ARTIFACT_TOTAL_BYTES = 50 * 1024 * 1024
JOB_SCRIPT_VERSION = "1"
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_MODEL_ALIAS = "ddu-narrator"
_BASE_URL = "http://ddu-llm:8080"
_NETWORK = "ddu_inference_bus"
_ALLOWED_CASES = {"all", "history", "medical"}
_ALLOWED_ERROR_CATEGORIES = {
    "timeout",
    "connection_error",
    "http_error",
    "invalid_response",
    "invalid_configuration",
    "invalid_models_response",
    "model_alias_mismatch",
    "request_failed",
    "output_too_large",
    "command_failed",
}
_ALLOWED_EXCEPTION_CLASSES = {
    "TimeoutError",
    "HTTPError",
    "HTTPResponse",
    "URLError",
    "ValueError",
    "OSError",
    "RuntimeError",
}
_ARTIFACT_FIELDS = {
    "kind",
    "timestamp_utc",
    "context_length",
    "concurrency",
    "case",
    "model",
    "outcome",
    "elapsed_seconds",
    "client_queue_wait_seconds",
    "queue_time_ms",
    "time_to_first_token_ms",
    "tokens_per_second",
    "completion_tokens",
    "request_count",
    "retry_count",
    "exception_class",
    "error",
    "http_status",
    "success_count",
    "success_rate",
    "wall_seconds",
    "p50_latency_seconds",
    "p95_latency_seconds",
    "p50_client_queue_wait_seconds",
    "p95_client_queue_wait_seconds",
    "p50_queue_time_ms",
    "p95_queue_time_ms",
    "p50_time_to_first_token_ms",
    "p95_time_to_first_token_ms",
    "output_tokens",
}
_NUMERIC_ARTIFACT_FIELDS = _ARTIFACT_FIELDS - {
    "kind",
    "timestamp_utc",
    "case",
    "model",
    "outcome",
    "exception_class",
    "error",
}
_LOGGER = logging.getLogger(__name__)


class JobValidationError(ValueError):
    """A job ID or option is outside the fixed registry."""


class ArtifactLimitError(RuntimeError):
    """A generated artifact exceeded its configured retention boundary."""


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    timeout_default: int
    timeout_min: int
    timeout_max: int
    defaults: dict[str, Any]
    allowed_options: frozenset[str]


class JobRegistry:
    """Fixed dashboard jobs and their bounded, typed options."""

    _specs: ClassVar[dict[str, JobSpec]] = {
        "server-readiness": JobSpec(
            job_id="server-readiness",
            timeout_default=30,
            timeout_min=1,
            timeout_max=120,
            defaults={"timeout_seconds": 30},
            allowed_options=frozenset({"timeout_seconds"}),
        ),
        "api-smoke": JobSpec(
            job_id="api-smoke",
            timeout_default=600,
            timeout_min=30,
            timeout_max=900,
            defaults={"case": "all", "timeout_seconds": 600, "max_tokens": 180},
            allowed_options=frozenset({"case", "timeout_seconds", "max_tokens"}),
        ),
        "performance-benchmark": JobSpec(
            job_id="performance-benchmark",
            timeout_default=1800,
            timeout_min=60,
            timeout_max=1800,
            defaults={
                "profile_id": "baseline-4k-2",
                "concurrency": 1,
                "diagnostic_concurrency": False,
                "requests_per_concurrency": 5,
                "warmup_per_case": 0,
                "timeout_seconds": 1800,
            },
            allowed_options=frozenset(
                {
                    "profile_id",
                    "concurrency",
                    "diagnostic_concurrency",
                    "requests_per_concurrency",
                    "warmup_per_case",
                    "timeout_seconds",
                }
            ),
        ),
    }

    @property
    def job_ids(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def validate(self, job_id: str, options: dict[str, Any] | None = None) -> tuple[JobSpec, dict[str, Any]]:
        if not isinstance(job_id, str) or job_id not in self._specs:
            raise JobValidationError("Requested job is not available")
        if options is None:
            options = {}
        if not isinstance(options, dict):
            raise JobValidationError("Job options are invalid")
        spec = self._specs[job_id]
        if not set(options) <= spec.allowed_options:
            raise JobValidationError("Job options are invalid")
        clean = {**spec.defaults, **options}
        timeout = clean["timeout_seconds"]
        if type(timeout) is not int or not spec.timeout_min <= timeout <= spec.timeout_max:
            raise JobValidationError("Job timeout is outside its allowed range")
        if job_id == "api-smoke":
            if not isinstance(clean["case"], str) or clean["case"] not in _ALLOWED_CASES:
                raise JobValidationError("Job options are invalid")
            if type(clean["max_tokens"]) is not int or not 1 <= clean["max_tokens"] <= 600:
                raise JobValidationError("Job token limit is outside its allowed range")
        elif job_id == "performance-benchmark":
            profile_id = clean["profile_id"]
            if not isinstance(profile_id, str) or profile_id not in {"baseline-4k-2", "baseline-8k-4"}:
                raise JobValidationError("Requested profile is not available")
            if type(clean["diagnostic_concurrency"]) is not bool:
                raise JobValidationError("Job options are invalid")
            if type(clean["concurrency"]) is not int or clean["concurrency"] not in {1, 2, 4}:
                raise JobValidationError("Benchmark concurrency is outside its allowed choices")
            if clean["concurrency"] == 4 and not clean["diagnostic_concurrency"]:
                raise JobValidationError("Concurrency four requires the diagnostic choice")
            requests = clean["requests_per_concurrency"]
            warmups = clean["warmup_per_case"]
            if type(requests) is not int or not 1 <= requests <= 25:
                raise JobValidationError("Benchmark request count is outside its allowed range")
            if type(warmups) is not int or not 0 <= warmups <= 2:
                raise JobValidationError("Benchmark warmup count is outside its allowed range")
        return spec, clean


class JobStore:
    """SQLite-backed job state and sanitized result summaries."""

    _fields: ClassVar[frozenset[str]] = frozenset({
        "state",
        "started_at",
        "completed_at",
        "summary_json",
        "error_category",
        "artifact_id",
        "exit_code",
    })

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata
        self.metadata.migrate(4, JOB_SCHEMA)

    def create(self, job_id: str, kind: str, options: dict[str, Any], now: float) -> dict[str, Any]:
        with self.metadata.transaction() as connection:
            connection.execute(
                "INSERT INTO dashboard_jobs "
                "(job_id, kind, state, created_at, options_json, summary_json) "
                "VALUES (?, ?, 'queued', ?, ?, '{}')",
                (job_id, kind, now, json.dumps(options, sort_keys=True)),
            )
        return self.get(job_id)

    def update(self, job_id: str, **values: Any) -> dict[str, Any]:
        if not values or not set(values) <= self._fields:
            raise ValueError("Invalid dashboard job update")
        clean = dict(values)
        if "summary_json" in clean and isinstance(clean["summary_json"], dict):
            clean["summary_json"] = json.dumps(clean["summary_json"], sort_keys=True)
        assignments = ", ".join(f"{field} = ?" for field in clean)
        with self.metadata.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE dashboard_jobs SET {assignments} WHERE job_id = ?",
                [*clean.values(), job_id],
            )
            if cursor.rowcount != 1:
                raise KeyError("Dashboard job is unavailable")
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self.metadata.transaction() as connection:
            row = connection.execute(
                "SELECT job_id, kind, state, created_at, started_at, completed_at, "
                "options_json, summary_json, error_category, artifact_id, exit_code "
                "FROM dashboard_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError("Dashboard job is unavailable")
        try:
            options = json.loads(row[6])
            summary = json.loads(row[7])
        except (TypeError, json.JSONDecodeError):
            options, summary = {}, {}
        return {
            "job_id": row[0],
            "kind": row[1],
            "state": row[2],
            "created_at": _timestamp(row[3]),
            "started_at": _timestamp(row[4]) if row[4] is not None else None,
            "completed_at": _timestamp(row[5]) if row[5] is not None else None,
            "options": options,
            "summary": summary,
            "error_category": row[8],
            "artifact_id": row[9],
            "exit_code": row[10],
        }

    def list(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(100, int(limit)))
        bounded_offset = max(0, int(offset))
        with self.metadata.transaction() as connection:
            rows = connection.execute(
                "SELECT job_id FROM dashboard_jobs ORDER BY created_at DESC, rowid DESC "
                "LIMIT ? OFFSET ?",
                (bounded_limit, bounded_offset),
            ).fetchall()
        return [self.get(row[0]) for row in rows]


class JobArtifactStore:
    """Private per-user job artifacts, with bounded bytes and retention."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        max_files: int = MAX_ARTIFACT_FILES,
        max_total_bytes: int = MAX_ARTIFACT_TOTAL_BYTES,
    ) -> None:
        if type(max_files) is not int or max_files < 1 or max_total_bytes < 1:
            raise ValueError("Artifact retention limits must be positive")
        self.root = Path(root) if root is not None else data_directory() / "jobs" / "artifacts"
        self.work_root = self.root.parent / ".job-work"
        self.max_files = max_files
        self.max_total_bytes = max_total_bytes
        ensure_private_directory(self.root)
        ensure_private_directory(self.work_root)

    def work_path(self, job_id: str) -> Path:
        _validate_job_id(job_id)
        job_dir = self.work_root / job_id
        ensure_private_directory(job_dir)
        return job_dir / "results.jsonl"

    def read_candidates(self, path: Path) -> list[dict[str, Any]]:
        resolved = Path(path).resolve()
        if resolved.parent.parent != self.work_root.resolve() or not resolved.is_file():
            return []
        try:
            if resolved.stat().st_size > MAX_ARTIFACT_BYTES:
                raise ArtifactLimitError("Job artifact exceeded its size limit")
            lines = resolved.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            return []
        if len(lines) > MAX_ARTIFACT_LINES:
            raise ArtifactLimitError("Job artifact exceeded its record limit")
        records: list[dict[str, Any]] = []
        for line in lines:
            if len(line.encode("utf-8")) > 16_384:
                raise ArtifactLimitError("Job artifact exceeded its line limit")
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    def publish(self, job_id: str, records: list[dict[str, Any]]) -> str:
        _validate_job_id(job_id)
        if len(records) > MAX_ARTIFACT_LINES:
            raise ArtifactLimitError("Job artifact exceeded its record limit")
        destination = self.root / f"{job_id}.jsonl"
        temporary = self.root / f".{job_id}.tmp"
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                for record in records:
                    clean = _sanitize_artifact_record(record)
                    encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    stream.write(encoded + "\n")
            if temporary.stat().st_size > MAX_ARTIFACT_BYTES:
                raise ArtifactLimitError("Job artifact exceeded its size limit")
            os.replace(temporary, destination)
            ensure_private_file(destination)
            self._retain()
        except (OSError, RuntimePathError):
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise RuntimeError("Job artifact storage is unavailable") from None
        except ArtifactLimitError:
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise
        return destination.name

    def read_text(self, artifact_id: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{32}\.jsonl", artifact_id):
            raise KeyError("Job artifact is unavailable")
        path = self.root / artifact_id
        if not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
            raise KeyError("Job artifact is unavailable")
        return path.read_text(encoding="utf-8")

    def list_artifacts(self) -> list[Path]:
        return sorted(self.root.glob("[0-9a-f]" * 32 + ".jsonl"), key=lambda path: path.stat().st_mtime)

    def cleanup_work(self, job_id: str) -> None:
        _validate_job_id(job_id)
        target = self.work_root / job_id
        if target.resolve().parent == self.work_root.resolve():
            shutil.rmtree(target, ignore_errors=True)

    def _retain(self) -> None:
        artifacts = self.list_artifacts()
        total = sum(path.stat().st_size for path in artifacts)
        while artifacts and (len(artifacts) > self.max_files or total > self.max_total_bytes):
            oldest = artifacts.pop(0)
            size = oldest.stat().st_size
            oldest.unlink(missing_ok=True)
            total -= size


@dataclass(frozen=True)
class JobCommandResult:
    return_code: int
    stdout: str
    output_too_large: bool = False


class DockerJobCommandAdapter:
    """Run the three fixed scripts in a bounded, network-only Docker client."""

    timeout = 15

    def __init__(
        self,
        *,
        docker_path: str | None,
        script_root: Path,
        profiles_path: Path | None = None,
    ) -> None:
        self.docker_path = docker_path
        self.script_root = Path(script_root).resolve()
        self.profiles_path = profiles_path
        self._container_names: dict[str, str] = {}

    def run(
        self,
        job_id: str,
        options: dict[str, Any],
        *,
        artifact_path: Path,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> JobCommandResult:
        argv, container_name = self._build_command(job_id, options, artifact_path)
        self._container_names[job_id] = container_name
        output = bytearray()
        output_too_large = threading.Event()

        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return JobCommandResult(127, '{"ok":false,"error":"command_failed"}')

        def collect_stdout() -> None:
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    return
                if len(output) + len(chunk) > MAX_OUTPUT_BYTES:
                    output_too_large.set()
                    return
                output.extend(chunk)

        reader = threading.Thread(target=collect_stdout, name=f"dashboard-job-output-{job_id[:8]}", daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout_seconds
        timed_out = False
        while process.poll() is None:
            if cancel_event.is_set() or output_too_large.is_set():
                self._stop_container(container_name)
                self._terminate(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                self._stop_container(container_name)
                self._terminate(process)
                break
            time.sleep(0.05)
        try:
            return_code = process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = process.wait(timeout=3)
        reader.join(timeout=3)
        if timed_out:
            return JobCommandResult(return_code, "{}")
        return JobCommandResult(
            return_code,
            output.decode("utf-8", errors="replace"),
            output_too_large=output_too_large.is_set(),
        )

    def cancel(self, job_id: str) -> None:
        name = self._container_names.get(job_id)
        if name:
            self._stop_container(name)

    def _build_command(
        self, job_id: str, options: dict[str, Any], artifact_path: Path
    ) -> tuple[list[str], str]:
        _validate_job_id(job_id)
        job_kind, clean = JobRegistry().validate(options.pop("_job_kind", ""), options)
        profile_id = clean.get("profile_id") or "baseline-4k-2"
        profile = load_allowlisted_profile(profile_id)
        image_ref = profile["image_ref"]
        if not isinstance(image_ref, str) or "@sha256:" not in image_ref:
            raise JobValidationError("The pinned client image is unavailable")
        script = {
            "server-readiness": "check_server.py",
            "api-smoke": "shared_inference_smoke.py",
            "performance-benchmark": "benchmark.py",
        }[job_kind.job_id]
        artifact_directory = Path(artifact_path).parent.resolve()
        script_mount = _mount_value(self.script_root, "/dashboard-scripts", readonly=True)
        artifact_mount = _mount_value(artifact_directory, "/artifacts", readonly=False)
        name = f"local-ai-dashboard-job-{job_id}"
        argv = [
            self.docker_path or "docker",
            "run",
            "--rm",
            "--pull=never",
            "--name",
            name,
            "--network",
            _NETWORK,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--mount",
            script_mount,
            "--mount",
            artifact_mount,
            "--entrypoint",
            "python3",
            image_ref,
            f"/dashboard-scripts/{script}",
        ]
        argv.extend(self._script_args(job_kind.job_id, clean, profile, artifact_path))
        return argv, name

    @staticmethod
    def _script_args(job_id: str, options: dict[str, Any], profile: dict[str, Any], artifact_path: Path) -> list[str]:
        if job_id == "server-readiness":
            return [
                "--base-url",
                _BASE_URL,
                "--model",
                _MODEL_ALIAS,
                "--timeout",
                "10",
                "--max-retries",
                "1",
            ]
        if job_id == "api-smoke":
            return [
                "--base-url",
                _BASE_URL,
                "--model",
                _MODEL_ALIAS,
                "--case",
                options["case"],
                "--timeout",
                "120",
                "--max-tokens",
                str(options["max_tokens"]),
                "--max-retries",
                "1",
            ]
        return [
            "--base-url",
            _BASE_URL,
            "--model",
            _MODEL_ALIAS,
            "--context-length",
            str(profile["max_model_len"]),
            "--concurrency",
            str(options["concurrency"]),
            "--requests-per-concurrency",
            str(options["requests_per_concurrency"]),
            "--warmup-per-case",
            str(options["warmup_per_case"]),
            "--max-tokens",
            "128",
            "--timeout",
            "120",
            "--max-retries",
            "1",
            "--disable-thinking",
            "--output",
            "/artifacts/results.jsonl",
        ]

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def _stop_container(self, name: str) -> None:
        try:
            subprocess.run(
                [self.docker_path or "docker", "stop", "--time", "2", name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return


class JobRunner:
    """Serialize bounded jobs against inference operations and retain safe results."""

    def __init__(self, gate: Any, command_adapter: Any, artifact_store: Any) -> None:
        self.gate = gate
        self.command_adapter = command_adapter
        self.artifact_store = artifact_store
        self.registry = JobRegistry()
        self.store = JobStore(gate.metadata)
        self._lock = threading.RLock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}

    def start(self, job_id: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        spec, clean = self.registry.validate(job_id, options)
        identifier = uuid4().hex
        now = time.time()
        self.store.create(identifier, job_id, clean, now)
        try:
            artifact_path = self.artifact_store.work_path(identifier)
        except Exception:  # noqa: BLE001 - artifact paths must not escape through error text.
            return self.store.update(
                identifier,
                state="failed",
                completed_at=time.time(),
                error_category="artifact_storage_unavailable",
            )
        lease_seconds = max(60, spec.timeout_max + 120)
        gate_kind = "benchmark" if job_id == "performance-benchmark" else "job"
        try:
            acquired = self.gate.acquire(gate_kind, identifier, now, lease_seconds)
        except Exception:  # noqa: BLE001 - keep adapter errors sanitized.
            acquired = False
        if not acquired:
            self.artifact_store.cleanup_work(identifier)
            return self.store.update(
                identifier,
                state="rejected",
                completed_at=time.time(),
                error_category="activity_conflict",
            )
        event = threading.Event()
        thread = threading.Thread(
            target=self._execute,
            args=(identifier, spec, clean, artifact_path, event),
            name=f"dashboard-job-{identifier[:8]}",
            daemon=True,
        )
        with self._lock:
            self._cancel_events[identifier] = event
            self._threads[identifier] = thread
        thread.start()
        return self.status(identifier)

    def cancel(self, job_id: str) -> bool:
        _validate_job_id(job_id)
        with self._lock:
            event = self._cancel_events.get(job_id)
            thread = self._threads.get(job_id)
            if event is None or thread is None or not thread.is_alive():
                return False
            event.set()
        cancel = getattr(self.command_adapter, "cancel", None)
        if callable(cancel):
            try:
                cancel(job_id)
            except Exception as exc:  # noqa: BLE001 - only a short exception class is logged.
                _LOGGER.warning("Job cancellation adapter failed (%s)", type(exc).__name__)
        return True

    def status(self, job_id: str) -> dict[str, Any]:
        _validate_job_id(job_id)
        return self.store.get(job_id)

    def history(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return self.store.list(limit, offset)

    def _execute(
        self,
        job_id: str,
        spec: JobSpec,
        options: dict[str, Any],
        artifact_path: Path,
        cancel_event: threading.Event,
    ) -> None:
        started = time.time()
        timeout_event = threading.Event()
        timer = threading.Timer(options["timeout_seconds"], lambda: _set_timeout(timeout_event, cancel_event))
        timer.daemon = True
        timer.start()
        try:
            self.store.update(job_id, state="running", started_at=started)
            command_options = {**options, "_job_kind": spec.job_id}
            result = self.command_adapter.run(
                job_id,
                command_options,
                artifact_path=artifact_path,
                timeout_seconds=options["timeout_seconds"],
                cancel_event=cancel_event,
            )
            if timeout_event.is_set():
                self._finish(job_id, "failed", "timeout", exit_code=getattr(result, "return_code", None))
                return
            if cancel_event.is_set():
                self._finish(
                    job_id, "cancelled", "cancelled_by_operator", exit_code=getattr(result, "return_code", None)
                )
                return
            stdout = getattr(result, "stdout", "")
            if getattr(result, "output_too_large", False) or not isinstance(stdout, str) or len(stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
                self._finish(
                    job_id, "failed", "output_too_large", exit_code=getattr(result, "return_code", None)
                )
                return
            return_code = getattr(result, "return_code", None)
            if type(return_code) is not int:
                self._finish(job_id, "failed", "command_failed", exit_code=None)
                return
            records, command_summary = _sanitize_command_output(spec.job_id, stdout)
            artifact_records = self.artifact_store.read_candidates(artifact_path)
            records.extend(_sanitize_artifact_record(record) for record in artifact_records)
            profile_id = options.get("profile_id") or self.gate.control_state.get_control_state().get(
                "active_profile_id"
            )
            provenance = _provenance(profile_id, options)
            summary = {
                **provenance,
                **command_summary,
                "duration_seconds": round(max(0.0, time.time() - started), 3),
                "script_version": JOB_SCRIPT_VERSION,
            }
            artifact_id = self.artifact_store.publish(job_id, records or [{"kind": spec.job_id, **summary}])
            if return_code == 0 and command_summary.get("ok") is True:
                self._finish(job_id, "completed", None, artifact_id, return_code, summary)
            else:
                category = command_summary.get("error")
                if spec.job_id == "api-smoke" and category == "command_failed":
                    category = next(
                        (record.get("error") for record in records if record.get("error")),
                        category,
                    )
                self._finish(
                    job_id,
                    "failed",
                    category if category in _ALLOWED_ERROR_CATEGORIES else "command_failed",
                    artifact_id,
                    return_code,
                    summary,
                )
        except ArtifactLimitError:
            self._finish(job_id, "failed", "artifact_too_large")
        except Exception:  # noqa: BLE001 - no output, exception text, or paths leave the runner.
            self._finish(job_id, "failed", "job_runner_error")
        finally:
            timer.cancel()
            try:
                self.artifact_store.cleanup_work(job_id)
            except Exception as exc:  # noqa: BLE001 - only a short exception class is logged.
                _LOGGER.warning("Job work cleanup failed (%s)", type(exc).__name__)
            try:
                self.gate.release(job_id)
            except Exception as exc:  # noqa: BLE001 - keep worker teardown independent.
                _LOGGER.error("Job activity release failed (%s)", type(exc).__name__)
            with self._lock:
                self._cancel_events.pop(job_id, None)
                self._threads.pop(job_id, None)

    def _finish(
        self,
        job_id: str,
        state: str,
        error_category: str | None,
        artifact_id: str | None = None,
        exit_code: int | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        self.store.update(
            job_id,
            state=state,
            completed_at=time.time(),
            error_category=error_category,
            artifact_id=artifact_id,
            exit_code=exit_code,
            summary_json=summary or {},
        )


def _sanitize_command_output(job_id: str, stdout: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        document = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return [], {"ok": False, "error": "invalid_response"}
    if not isinstance(document, dict):
        return [], {"ok": False, "error": "invalid_response"}
    records: list[dict[str, Any]] = []
    if job_id == "server-readiness":
        requests = _number(document.get("requests"), integer=True)
        return (
            [
                {
                    "kind": "summary",
                    "model": _MODEL_ALIAS,
                    "outcome": "success" if document.get("ok") is True else "failure",
                    "request_count": requests,
                }
            ],
            {
                "ok": document.get("ok") is True and document.get("model") == _MODEL_ALIAS,
                "request_count": requests,
                "error": _safe_category(document.get("error")),
            },
        )
    if job_id == "api-smoke":
        results = document.get("results")
        if not isinstance(results, list) or len(results) > 20:
            return [], {"ok": False, "error": "invalid_response"}
        successes = 0
        failures = 0
        for result in results:
            if not isinstance(result, dict):
                continue
            record = _sanitize_artifact_record(
                {
                    "kind": "request",
                    "case": result.get("case"),
                    "model": result.get("model"),
                    "outcome": "success" if result.get("ok") is True else "failure",
                    "elapsed_seconds": result.get("elapsed_seconds"),
                    "request_count": result.get("request_count"),
                    "retry_count": result.get("retry_count"),
                    "exception_class": result.get("exception_class"),
                    "error": result.get("error"),
                    "http_status": result.get("http_status"),
                }
            )
            records.append(record)
            successes += record["outcome"] == "success"
            failures += record["outcome"] == "failure"
        okay = document.get("ok") is True and failures == 0 and bool(records)
        return records, {
            "ok": okay,
            "request_count": len(records),
            "success_count": successes,
            "error_count": failures,
            "error": None if okay else "command_failed",
        }
    summaries = document.get("summaries")
    if not isinstance(summaries, list) or len(summaries) > 3:
        return [], {"ok": False, "error": "invalid_response"}
    sanitized = [_sanitize_artifact_record({"kind": "summary", **item}) for item in summaries if isinstance(item, dict)]
    request_count = sum(int(record.get("request_count", 0)) for record in sanitized)
    success_count = sum(int(record.get("success_count", 0)) for record in sanitized)
    ok = document.get("ok") is True and bool(sanitized) and all(record.get("success_rate") == 1 for record in sanitized)
    return records, {
        "ok": ok,
        "request_count": request_count,
        "success_count": success_count,
        "error_count": max(0, request_count - success_count),
        "latency_percentiles": {
            "p50_seconds": _number(sanitized[-1].get("p50_latency_seconds"), integer=False),
            "p95_seconds": _number(sanitized[-1].get("p95_latency_seconds"), integer=False),
        },
        "error": None if ok else "command_failed",
    }


def _sanitize_artifact_record(record: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key in _ARTIFACT_FIELDS & set(record):
        value = record[key]
        if key == "kind":
            if value in {"request", "warmup", "summary", "server-readiness", "api-smoke", "performance-benchmark"}:
                clean[key] = value
        elif key == "case":
            if value in {"history", "medical", "all"}:
                clean[key] = value
        elif key == "model":
            if value == _MODEL_ALIAS:
                clean[key] = _MODEL_ALIAS
        elif key == "outcome":
            if value in {"success", "failure"}:
                clean[key] = value
        elif key == "error":
            category = _safe_category(value)
            if category is not None:
                clean[key] = category
        elif key == "exception_class":
            if value in _ALLOWED_EXCEPTION_CLASSES:
                clean[key] = value
        elif key == "timestamp_utc":
            # Never trust timestamps embedded in command output.
            clean[key] = _timestamp(time.time())
        elif key in _NUMERIC_ARTIFACT_FIELDS:
            number = _number(value, integer=key in {"context_length", "concurrency", "completion_tokens", "request_count", "retry_count", "http_status", "success_count", "output_tokens"})
            if number is not None:
                clean[key] = number
    return clean


def _provenance(profile_id: str | None, options: dict[str, Any]) -> dict[str, Any]:
    profile: dict[str, Any] | None = None
    if profile_id in {"baseline-4k-2", "baseline-8k-4"}:
        try:
            profile = load_allowlisted_profile(profile_id)
        except Exception:  # noqa: BLE001 - provenance must not break a job.
            profile = None
    try:
        telemetry = read_host_telemetry()
    except Exception:  # noqa: BLE001 - telemetry is optional provenance.
        telemetry = {"gpu": {"available": False}}
    image = profile.get("image_ref") if profile else None
    return {
        "profile_id": profile_id,
        "model_revision": profile.get("model_revision") if profile else None,
        "engine": profile.get("engine") if profile else None,
        "engine_image_digest": image.split("@", 1)[1] if isinstance(image, str) and "@" in image else None,
        "context_length": profile.get("max_model_len") if profile else None,
        "max_num_seqs": profile.get("max_num_seqs") if profile else None,
        "host": {"platform": platform.system(), "architecture": platform.machine()},
        "telemetry": telemetry,
        "concurrency": options.get("concurrency", 1),
        "script_version": JOB_SCRIPT_VERSION,
    }


def _mount_value(path: Path, target: str, *, readonly: bool) -> str:
    resolved = Path(path).resolve()
    source = str(resolved)
    if any(character in source for character in (",", "\r", "\n")):
        raise JobValidationError("A safe Docker mount path is unavailable")
    suffix = ",readonly" if readonly else ""
    return f"type=bind,source={source},target={target}{suffix}"


def _validate_job_id(job_id: str) -> None:
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        raise KeyError("Dashboard job is unavailable")


def _number(value: Any, *, integer: bool) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    if integer:
        return int(value) if int(value) == value else None
    return round(float(value), 3)


def _safe_category(value: Any) -> str | None:
    return value if isinstance(value, str) and value in _ALLOWED_ERROR_CATEGORIES else None


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _set_timeout(timeout_event: threading.Event, cancel_event: threading.Event) -> None:
    timeout_event.set()
    cancel_event.set()
