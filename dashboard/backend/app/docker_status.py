from __future__ import annotations

import json
import math
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .profiles import load_profiles, match_profile

PROJECT_NAME = "local-ai-api-lite"
SERVICE_NAME = "vllm"
_COMMAND_FLAGS = {
    "--revision": "model_revision",
    "--served-model-name": "model_alias",
    "--reasoning-parser": "reasoning_parser",
    "--max-model-len": "max_model_len",
    "--max-num-seqs": "max_num_seqs",
    "--gpu-memory-utilization": "gpu_memory_utilization",
}
_MEMORY_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}


class DockerReader:
    """Read the one fixed Compose service through bounded Docker CLI queries."""

    def __init__(
        self,
        docker_path: str | None = None,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout_seconds: float = 3,
        project_name: str = PROJECT_NAME,
        service_name: str = SERVICE_NAME,
    ) -> None:
        self.docker_path = docker_path
        self._run = run
        self.timeout_seconds = timeout_seconds
        self.project_name = project_name
        self.service_name = service_name

    def _command(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        if not self.docker_path:
            raise FileNotFoundError("Docker CLI is unavailable")
        return self._run(
            [self.docker_path, *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=self.timeout_seconds,
        )

    @staticmethod
    def _checked(result: subprocess.CompletedProcess[str]) -> str:
        if result.returncode != 0:
            raise RuntimeError("Docker query failed")
        return result.stdout or ""

    def read(self) -> dict[str, Any]:
        try:
            listing = self._checked(
                self._command(
                    "ps",
                    "--all",
                    "--quiet",
                    "--filter",
                    f"label=com.docker.compose.project={self.project_name}",
                    "--filter",
                    f"label=com.docker.compose.service={self.service_name}",
                )
            )
            container_ids = [line.strip() for line in listing.splitlines() if line.strip()]
            if not container_ids:
                return self._empty_result("Stopped", "available")
            if len(container_ids) != 1:
                return {
                    **self._empty_result("Degraded", "available"),
                    "last_failure_reason": "Multiple inference containers match the LITE Compose service",
                }

            inspect_text = self._checked(self._command("inspect", container_ids[0]))
            records = json.loads(inspect_text)
            if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
                raise ValueError("Unexpected Docker inspect response")
            return self._from_inspect(records[0])
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return {
                **self._empty_result("Unknown", "unavailable"),
                "last_failure_reason": f"Docker status unavailable ({type(exc).__name__})",
            }

    @staticmethod
    def _empty_result(state: str, availability: str) -> dict[str, Any]:
        return {
            "availability": availability,
            "inference_state": state,
            "container": None,
            "configuration": None,
            "active_profile": None,
            "last_health_probe_at": None,
            "last_successful_health_probe_at": None,
            "last_failure_reason": None,
            "cache_path": None,
        }

    def _from_inspect(self, record: dict[str, Any]) -> dict[str, Any]:
        state_data = record.get("State") if isinstance(record.get("State"), dict) else {}
        state = str(state_data.get("Status", "unknown")).lower()
        health_data = state_data.get("Health") if isinstance(state_data.get("Health"), dict) else {}
        health = str(health_data.get("Status", "unavailable")).lower()
        exit_code = _integer(state_data.get("ExitCode"))
        health_logs = health_data.get("Log", [])
        last_probe, last_success, latest_failed_code = _health_probe_times(health_logs)

        config = record.get("Config") if isinstance(record.get("Config"), dict) else {}
        configuration = _configuration_from_command(config.get("Cmd"), config.get("Image"))
        try:
            profiles = load_profiles()
        except (ValueError, TypeError):
            profiles = []
        active_profile = match_profile(configuration, profiles)

        ports = _host_published_ports(record)
        networks = record.get("NetworkSettings", {}).get("Networks", {})
        aliases = sorted(
            {
                str(alias)
                for network in networks.values()
                if isinstance(network, dict)
                for alias in network.get("Aliases", [])
                if isinstance(alias, str)
            }
        ) if isinstance(networks, dict) else []

        if state == "running":
            inference_state = "Ready" if health == "healthy" else "Starting" if health == "starting" else "Degraded"
        elif state in {"created", "restarting"}:
            inference_state = "Starting"
        elif state == "exited":
            inference_state = "Stopped" if exit_code == 0 else "Failed"
        elif state == "dead":
            inference_state = "Failed"
        elif state == "paused":
            inference_state = "Degraded"
        else:
            inference_state = "Unknown"

        failure_reason: str | None = None
        if state == "running" and health == "unhealthy":
            failure_code = latest_failed_code if latest_failed_code is not None else 1
            failure_reason = f"Container healthcheck failed (exit code {failure_code})"
        elif inference_state == "Failed":
            failure_reason = f"Container exited with code {exit_code}" if exit_code is not None else "Inference container failed"
        elif ports:
            inference_state = "Degraded"
            failure_reason = "Inference port is published on the host"
        elif "ddu-llm" not in aliases and networks:
            inference_state = "Degraded"
            failure_reason = "The expected ddu-llm network alias is unavailable"

        container_id = record.get("Id") if isinstance(record.get("Id"), str) else ""
        container_name = str(record.get("Name", "")).lstrip("/")
        stats = self._read_stats(container_id) if state == "running" and container_id else {}
        cache_path = _cache_mount_source(record.get("Mounts"))

        return {
            "availability": "available",
            "inference_state": inference_state,
            "container": {
                "name": container_name,
                "state": state.title(),
                "health": health.title() if health != "unavailable" else "Unavailable",
                "uptime_seconds": _uptime_seconds(state_data.get("StartedAt")) if state == "running" else None,
                "restart_count": _integer(record.get("RestartCount")) or 0,
                "cpu_percent": stats.get("cpu_percent"),
                "memory_used_bytes": stats.get("memory_used_bytes"),
                "memory_limit_bytes": stats.get("memory_limit_bytes"),
                "host_published_ports": ports,
            },
            "configuration": configuration,
            "active_profile": active_profile,
            "last_health_probe_at": last_probe,
            "last_successful_health_probe_at": last_success,
            "last_failure_reason": failure_reason,
            "cache_path": cache_path,
        }

    def _read_stats(self, container_id: str) -> dict[str, Any]:
        try:
            result = self._command("stats", "--no-stream", "--format", "{{json .}}", container_id)
            output = self._checked(result).strip()
            row = json.loads(output) if output else {}
            if not isinstance(row, dict):
                return {}
            return {
                "cpu_percent": _parse_percent(row.get("CPUPerc")),
                "memory_used_bytes": _parse_memory_part(row.get("MemUsage"), 0),
                "memory_limit_bytes": _parse_memory_part(row.get("MemUsage"), 1),
            }
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, TypeError, json.JSONDecodeError):
            return {}


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_percent(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*", value)
    if not match:
        return None
    result = float(match.group(1))
    return round(result, 2) if math.isfinite(result) and 0 <= result <= 10000 else None


def _parse_memory_part(value: Any, index: int) -> int | None:
    if not isinstance(value, str):
        return None
    parts = value.split("/")
    if len(parts) != 2 or index not in (0, 1):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?i?b)\s*", parts[index], re.IGNORECASE)
    if not match:
        return None
    unit = match.group(2).lower()
    amount = float(match.group(1))
    return int(amount * _MEMORY_UNITS[unit]) if math.isfinite(amount) and amount >= 0 else None


def _configuration_from_command(command: Any, image: Any) -> dict[str, Any] | None:
    if not isinstance(command, list) or not command or not isinstance(command[0], str):
        return None
    result: dict[str, Any] = {
        "model_id": command[0][:256],
        "model_revision": None,
        "model_alias": None,
        "reasoning_parser": None,
        "image_ref": str(image or "")[:256],
        "max_model_len": None,
        "max_num_seqs": None,
        "gpu_memory_utilization": None,
        "engine": "Unknown",
        "quantization": "W4A16" if re.search(r"(?:^|[._-])w4a16(?:$|[._-])", command[0], re.IGNORECASE) else "Unknown",
    }
    version_match = re.search(r"vllm-openai:v?(\d+\.\d+\.\d+)(?:@sha256:[a-f0-9]{64})?", str(image or ""))
    if version_match:
        result["engine"] = f"vLLM {version_match.group(1)}"

    index = 1
    while index < len(command):
        flag = command[index]
        key = _COMMAND_FLAGS.get(flag)
        if key is not None and index + 1 < len(command):
            raw = command[index + 1]
            try:
                if key in {"max_model_len", "max_num_seqs"}:
                    parsed: Any = int(raw)
                elif key == "gpu_memory_utilization":
                    parsed = float(raw)
                    if not math.isfinite(parsed) or not 0 < parsed <= 1:
                        parsed = None
                else:
                    parsed = str(raw)[:128]
                result[key] = parsed
            except (TypeError, ValueError, OverflowError):
                result[key] = None
            index += 2
        else:
            index += 1
    if all(result.get(key) is None for key in ("model_revision", "model_alias", "max_model_len", "max_num_seqs")):
        return result if result.get("model_id") else None
    return result


def _health_probe_times(logs: Any) -> tuple[str | None, str | None, int | None]:
    if not isinstance(logs, list):
        return None, None, None
    valid: list[tuple[str, int]] = []
    for entry in logs:
        if not isinstance(entry, dict):
            continue
        ended = entry.get("End")
        code = _integer(entry.get("ExitCode"))
        if isinstance(ended, str) and code is not None:
            valid.append((ended, code))
    latest = valid[-1] if valid else None
    successes = [ended for ended, code in valid if code == 0]
    latest_failed = next((code for _, code in reversed(valid) if code != 0), None)
    return (latest[0] if latest else None, successes[-1] if successes else None, latest_failed)


def _uptime_seconds(started_at: Any) -> int | None:
    if not isinstance(started_at, str) or not started_at or started_at.startswith("0001-"):
        return None
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - started.astimezone(UTC)).total_seconds()))


def _host_published_ports(record: dict[str, Any]) -> list[str]:
    network_settings = record.get("NetworkSettings")
    ports = network_settings.get("Ports") if isinstance(network_settings, dict) else None
    if not isinstance(ports, dict):
        return []
    published: list[str] = []
    for container_port, bindings in ports.items():
        if isinstance(bindings, list):
            for binding in bindings:
                if isinstance(binding, dict) and binding.get("HostPort"):
                    published.append(f"{container_port}->{binding['HostPort']}")
    return published


def _cache_mount_source(mounts: Any) -> str | None:
    if not isinstance(mounts, list):
        return None
    for mount in mounts:
        if isinstance(mount, dict) and mount.get("Destination") == "/root/.cache/huggingface":
            source = mount.get("Source")
            return source if isinstance(source, str) else None
    return None
