from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .docker_status import (
    _host_published_ports,
    _integer,
    _parse_memory_part,
    _parse_percent,
    _uptime_seconds,
)

SPEECH_PROJECT_NAME = "local-ai-api-lite-speech"
SPEECH_IMAGE_REF = "local-ai-api-lite-speech:0.1.0"
SPEECH_NETWORK_NAME = "ddu_inference_bus"
SERVICE_SPECS: dict[str, dict[str, str]] = {
    "stt": {
        "service": "stt",
        "alias": "ddu-stt",
        "project_name": SPEECH_PROJECT_NAME,
        "model_id": "Systran/faster-whisper-small",
        "revision": "536b0662742c02347bc0e980a01041f333bce120",
        "repository": "Systran/faster-whisper-small",
    },
    "tts": {
        "service": "tts",
        "alias": "ddu-tts",
        "project_name": SPEECH_PROJECT_NAME,
        "model_id": "rhasspy/piper-voices",
        "revision": "375a0fe641dea077c2a47b4e9a056d6da521eed3",
        "repository": "rhasspy/piper-voices · da: da_DK-talesyntese-medium; en: en_US-ljspeech-medium",
    },
}
_METRIC_INTEGER_FIELDS = {
    "accepted_total",
    "completed_total",
    "failed_total",
    "timed_out_total",
    "rejected_total",
    "active_requests",
    "queued_requests",
    "outstanding_requests",
    "max_outstanding",
}
_METRIC_BOOLEAN_FIELDS = {"saturated", "stuck_inference", "closed"}
_PROBE_SCRIPT = (
    "import json,urllib.request,urllib.error\n"
    "def get(p):\n"
    " try:\n"
    "  with urllib.request.urlopen('http://127.0.0.1:8080'+p,timeout=1.2) as r:\n"
    "   return r.status,json.loads(r.read(32768))\n"
    " except urllib.error.HTTPError as e:\n"
    "  try: body=json.loads(e.read(4096))\n"
    "  except Exception: body={}\n"
    "  return e.code,body\n"
    " except Exception:\n"
    "  return 0,{}\n"
    "health_status,health=get('/health'); ready_status,ready=get('/ready'); "
    "models_status,models=get('/v1/models'); metrics_status,metrics=get('/metrics'); "
    "items=models.get('data',[]) if isinstance(models,dict) else []; "
    "identity=[{'id':x.get('id'),'model_id':x.get('model_id'),'revision':x.get('revision')} for x in items "
    "if isinstance(x,dict)][:2] if isinstance(items,list) else []; "
    "fields=('accepted_total','completed_total','failed_total','timed_out_total','rejected_total',"
    "'active_requests','queued_requests','outstanding_requests','max_outstanding','saturated',"
    "'stuck_inference','closed','last_successful_inference_at'); "
    "safe={k:v for k,v in metrics.items() if k in fields} if isinstance(metrics,dict) else {}; "
    "print(json.dumps({'health_status':health_status,'ready_status':ready_status,"
    "'ready_state':ready.get('status') if isinstance(ready,dict) else None,"
    "'models_status':models_status,'models':identity,'metrics_status':metrics_status,"
    "'metrics':safe},separators=(',',':')))"
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_metrics(value: Any) -> dict[str, int | bool | str | None] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, int | bool | str | None] = {}
    for field in _METRIC_INTEGER_FIELDS:
        item = value.get(field)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            return None
        result[field] = item
    for field in _METRIC_BOOLEAN_FIELDS:
        item = value.get(field)
        if not isinstance(item, bool):
            return None
        result[field] = item
    last_success = value.get("last_successful_inference_at")
    if last_success is not None and (not isinstance(last_success, str) or len(last_success) > 40):
        return None
    result["last_successful_inference_at"] = last_success
    return result


def _unknown_service(spec: dict[str, str], category: str = "docker_unavailable") -> dict[str, Any]:
    return {
        "service": spec["service"],
        "alias": spec["alias"],
        "project_name": spec["project_name"],
        "state": "Unknown/Unavailable",
        "availability": "unavailable",
        "container": None,
        "image_ref": None,
        "image_id": None,
        "model_id": None,
        "model_revision": None,
        "expected_model_id": spec["model_id"],
        "expected_model_revision": spec["revision"],
        "model_repository": spec["repository"],
        "last_successful_readiness_check_at": None,
        "last_error_category": category,
        "metrics": None,
        "metrics_availability": "unavailable",
        "observed_at": None,
    }


def unknown_speech_service_snapshots() -> list[dict[str, Any]]:
    return [_unknown_service(spec) for spec in SERVICE_SPECS.values()]


class SpeechServiceDockerReader:
    """Read fixed speech containers and their bounded readiness/metric endpoints."""

    def __init__(
        self,
        docker_path: str | None = "docker",
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        *,
        timeout_seconds: float = 1.5,
        command_budget: float = 5.0,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], str] = _now,
    ) -> None:
        self.docker_path = docker_path
        self._run = run
        self.timeout_seconds = timeout_seconds
        self.command_budget = command_budget
        self._monotonic = monotonic_clock
        self._wall_clock = wall_clock
        self._last_success: dict[str, str | None] = {name: None for name in SERVICE_SPECS}

    def read(self, deadline: float | None = None) -> dict[str, dict[str, Any]]:
        overall_deadline = deadline if deadline is not None else self._monotonic() + 2 * self.command_budget
        return {
            name: self.read_service(name, deadline=min(overall_deadline, self._monotonic() + self.command_budget))
            for name in SERVICE_SPECS
        }

    def _command(self, arguments: list[str], deadline: float) -> subprocess.CompletedProcess[str]:
        if not self.docker_path:
            raise FileNotFoundError("Docker CLI is unavailable")
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired([self.docker_path, *arguments], timeout=0)
        return self._run(
            [self.docker_path, *arguments],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=min(self.timeout_seconds, remaining),
        )

    @staticmethod
    def _checked(result: subprocess.CompletedProcess[str]) -> str:
        if result.returncode != 0:
            raise RuntimeError("Docker query failed")
        return result.stdout or ""

    def _unknown(self, spec: dict[str, str], category: str) -> dict[str, Any]:
        snapshot = _unknown_service(spec, category)
        snapshot["last_successful_readiness_check_at"] = self._last_success[spec["service"]]
        snapshot["observed_at"] = self._wall_clock()
        return snapshot

    def read_service(self, service_name: str, deadline: float | None = None) -> dict[str, Any]:
        spec = SERVICE_SPECS.get(service_name)
        if spec is None:
            raise KeyError("Speech service is not allowlisted")
        deadline = deadline if deadline is not None else self._monotonic() + self.command_budget
        try:
            listing = self._checked(
                self._command(
                    [
                        "ps", "--all", "--quiet",
                        "--filter", f"label=com.docker.compose.project={spec['project_name']}",
                        "--filter", f"label=com.docker.compose.service={service_name}",
                    ],
                    deadline,
                )
            )
            container_ids = [line.strip() for line in listing.splitlines() if line.strip()]
            if not container_ids:
                return self._snapshot(spec, None, "Stopped", None)
            if len(container_ids) != 1 or any(not _CONTAINER_ID.fullmatch(value) for value in container_ids):
                return self._snapshot(spec, None, "Degraded", "container_identity_invalid")
            return self._read_container(spec, container_ids[0], deadline)
        except subprocess.TimeoutExpired:
            return self._unknown(spec, "command_timeout")
        except (OSError, subprocess.SubprocessError):
            return self._unknown(spec, "docker_unavailable")
        except (RuntimeError, TypeError, ValueError, json.JSONDecodeError):
            return self._unknown(spec, "docker_response_invalid")

    def _read_container(self, spec: dict[str, str], container_id: str, deadline: float) -> dict[str, Any]:
        records = json.loads(self._checked(self._command(["inspect", container_id], deadline)))
        if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
            raise ValueError("Docker inspect response is invalid")
        record = records[0]
        config = record.get("Config") if isinstance(record.get("Config"), dict) else {}
        labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
        if (
            labels.get("com.docker.compose.project") != spec["project_name"]
            or labels.get("com.docker.compose.service") != spec["service"]
        ):
            return self._snapshot(spec, record, "Degraded", "compose_identity_mismatch")

        state_data = record.get("State") if isinstance(record.get("State"), dict) else {}
        runtime_state = str(state_data.get("Status", "unknown")).lower()
        health_data = state_data.get("Health") if isinstance(state_data.get("Health"), dict) else {}
        health = str(health_data.get("Status", "unavailable")).lower()
        if runtime_state != "running":
            if runtime_state in {"created", "restarting"}:
                return self._snapshot(spec, record, "Starting/Loading", "container_starting")
            if runtime_state == "exited" and _integer(state_data.get("ExitCode")) == 0:
                return self._snapshot(spec, record, "Stopped", None)
            if runtime_state in {"exited", "dead", "paused", "removing"}:
                return self._snapshot(spec, record, "Degraded", "container_not_running")
            return self._unknown(spec, "container_state_unknown")

        stats = self._read_stats(container_id, deadline)
        if config.get("Image") != SPEECH_IMAGE_REF:
            return self._snapshot(spec, record, "Degraded", "image_identity_mismatch", stats=stats)
        if _host_published_ports(record):
            return self._snapshot(spec, record, "Degraded", "host_port_published", stats=stats)
        networks = record.get("NetworkSettings", {}).get("Networks", {})
        network = networks.get(SPEECH_NETWORK_NAME) if isinstance(networks, dict) else None
        aliases = network.get("Aliases") if isinstance(network, dict) else None
        if not isinstance(aliases, list) or spec["alias"] not in aliases:
            return self._snapshot(spec, record, "Degraded", "network_alias_mismatch", stats=stats)
        mounts = record.get("Mounts")
        cache_mounts = [
            mount for mount in mounts
            if isinstance(mount, dict)
            and mount.get("Type") == "bind"
            and mount.get("Destination") == "/models"
            and mount.get("RW") is False
        ] if isinstance(mounts, list) else []
        if len(cache_mounts) != 1:
            return self._snapshot(spec, record, "Degraded", "model_cache_mount_mismatch", stats=stats)
        host_config = record.get("HostConfig") if isinstance(record.get("HostConfig"), dict) else {}
        if host_config.get("Privileged") is True or host_config.get("DeviceRequests"):
            return self._snapshot(spec, record, "Degraded", "runtime_privilege_mismatch", stats=stats)

        probe = json.loads(self._checked(self._command(["exec", container_id, "python", "-c", _PROBE_SCRIPT], deadline)))
        if not isinstance(probe, dict):
            raise TypeError("Speech worker contract response is invalid")
        if probe.get("health_status") != 200:
            return self._snapshot(spec, record, "Degraded", "liveness_check_failed", stats=stats)
        metrics = _safe_metrics(probe.get("metrics")) if probe.get("metrics_status") == 200 else None
        model_records = probe.get("models") if isinstance(probe.get("models"), list) else []
        model = next(
            (item for item in model_records if isinstance(item, dict) and item.get("id") == spec["alias"]),
            {},
        )
        model_id = model.get("model_id") if isinstance(model.get("model_id"), str) else None
        revision = model.get("revision") if isinstance(model.get("revision"), str) else None
        if probe.get("ready_status") != 200:
            ready_state = probe.get("ready_state")
            if ready_state == "loading":
                state, error = "Starting/Loading", "model_loading"
            elif ready_state in {"failed", "missing_artifact"}:
                state, error = "Degraded", "model_load_failed"
            elif ready_state == "degraded":
                if metrics and metrics.get("stuck_inference") is True:
                    error = "inference_stuck"
                elif metrics and metrics.get("saturated") is True:
                    error = "queue_saturated"
                else:
                    error = "worker_degraded"
                state = "Degraded"
            else:
                state, error = "Degraded", "readiness_check_failed"
            return self._snapshot(spec, record, state, error, model_id, revision, metrics, stats)
        if probe.get("models_status") != 200 or model_id != spec["model_id"] or revision != spec["revision"]:
            error = "model_identity_mismatch" if model_id or revision else "model_identity_unavailable"
            return self._snapshot(spec, record, "Degraded", error, model_id, revision, metrics, stats)
        if health != "healthy":
            state = "Starting/Loading" if health == "starting" else "Degraded"
            return self._snapshot(spec, record, state, "docker_liveness_unhealthy", model_id, revision, metrics, stats)

        self._last_success[spec["service"]] = self._wall_clock()
        error = "metrics_unavailable" if metrics is None else None
        return self._snapshot(spec, record, "Ready", error, model_id, revision, metrics, stats)

    def _snapshot(
        self,
        spec: dict[str, str],
        record: dict[str, Any] | None,
        state: str,
        error_category: str | None,
        model_id: str | None = None,
        revision: str | None = None,
        metrics: dict[str, int | bool | str | None] | None = None,
        stats: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "service": spec["service"],
            "alias": spec["alias"],
            "project_name": spec["project_name"],
            "state": state,
            "availability": "available",
            "container": _container_status(record, stats or {}) if record is not None else None,
            "image_ref": _config_image(record),
            "image_id": record.get("Image") if isinstance(record, dict) else None,
            "model_id": model_id,
            "model_revision": revision,
            "expected_model_id": spec["model_id"],
            "expected_model_revision": spec["revision"],
            "model_repository": spec["repository"],
            "last_successful_readiness_check_at": self._last_success[spec["service"]],
            "last_error_category": error_category,
            "metrics": metrics,
            "metrics_availability": "available" if metrics is not None else "unavailable",
            "observed_at": self._wall_clock(),
        }

    def _read_stats(self, container_id: str, deadline: float) -> dict[str, Any]:
        try:
            output = self._checked(
                self._command(["stats", "--no-stream", "--format", "{{json .}}", container_id], deadline)
            ).strip()
            row = json.loads(output) if output else {}
            if not isinstance(row, dict):
                return {}
            return {
                "cpu_percent": _parse_percent(row.get("CPUPerc")),
                "memory_used_bytes": _parse_memory_part(row.get("MemUsage"), 0),
                "memory_limit_bytes": _parse_memory_part(row.get("MemUsage"), 1),
            }
        except (OSError, subprocess.SubprocessError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
            return {}


_CONTAINER_ID = re.compile(r"^[0-9a-fA-F]{12,64}$")


def _config_image(record: dict[str, Any] | None) -> str | None:
    config = record.get("Config") if isinstance(record, dict) and isinstance(record.get("Config"), dict) else {}
    image = config.get("Image")
    return image[:256] if isinstance(image, str) else None


def _container_status(record: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    state = record.get("State") if isinstance(record.get("State"), dict) else {}
    health_data = state.get("Health") if isinstance(state.get("Health"), dict) else {}
    health = health_data.get("Status")
    return {
        "name": str(record.get("Name", "")).lstrip("/")[:128],
        "state": str(state.get("Status", "unknown")).title()[:32],
        "health": str(health).title() if isinstance(health, str) else "Unavailable",
        "uptime_seconds": _uptime_seconds(state.get("StartedAt")) if state.get("Status") == "running" else None,
        "restart_count": _integer(record.get("RestartCount")) or 0,
        "cpu_percent": stats.get("cpu_percent"),
        "memory_used_bytes": stats.get("memory_used_bytes"),
        "memory_limit_bytes": stats.get("memory_limit_bytes"),
        "host_published_ports": _host_published_ports(record),
    }
