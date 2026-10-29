from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from typing import Any

from .configuration import PROJECT_NAME, SERVICE_NAME

MAX_LOG_TAIL = 200
MAX_LOG_OUTPUT_CHARS = 131072
MAX_LOG_LINE_CHARS = 2048
_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$", re.IGNORECASE)
_SECRET_ENV_NAME = re.compile(
    r"(?:PASSWORD|PASSWD|TOKEN|API[_-]?KEY|SECRET|AUTH|CREDENTIAL|DSN|DATABASE_URL)",
    re.IGNORECASE,
)
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)\b(password|passwd|token|api[_-]?key|secret|authorization|credential|dsn|database_url)"
    r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER_SECRET = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_URL_CREDENTIAL = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s:]+:[^/@\s]+@")
_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]:\\|\\\\)[^\s\"'<>]+")
_UNIX_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:[^/\s\"'<>]+/)+[^/\s\"'<>]*")
_CONTENT_MARKER = re.compile(
    r"(?i)(?:\"(?:prompt|completion|messages|request[_ -]?body)\"|"
    r"'(?:prompt|completion|messages|request[_ -]?body)'|"
    r"\b(?:prompt|completion|messages|request[_ -]?body))\s*[:=]"
)


class DockerLogReader:
    """Read a small, sanitized tail from the fixed inference Compose service."""

    def __init__(
        self,
        docker_path: str | None = None,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout_seconds: float = 3,
        max_tail: int = MAX_LOG_TAIL,
    ) -> None:
        self.docker_path = docker_path
        self._run = run
        self.timeout_seconds = timeout_seconds
        self.max_tail = max(1, min(int(max_tail), MAX_LOG_TAIL))

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

    def read(self, *, tail: int = 100) -> dict[str, Any]:
        bounded_tail = max(1, min(int(tail), self.max_tail))
        try:
            listing = self._command(
                "ps",
                "--all",
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={PROJECT_NAME}",
                "--filter",
                f"label=com.docker.compose.service={SERVICE_NAME}",
            )
            if listing.returncode != 0:
                return self._unavailable("docker_query_failed")
            container_ids = [line.strip() for line in (listing.stdout or "").splitlines() if line.strip()]
            if not container_ids:
                return {"availability": "available", "lines": [], "error_category": None}
            if len(container_ids) != 1 or not _CONTAINER_ID.fullmatch(container_ids[0]):
                return self._unavailable("service_ambiguous")

            inspected = self._command(
                "inspect",
                "--format",
                "{{json .Config.Env}}",
                container_ids[0],
            )
            if inspected.returncode != 0:
                return self._unavailable("environment_unavailable")
            environment = json.loads(inspected.stdout or "null")
            if not isinstance(environment, list) or any(not isinstance(value, str) for value in environment):
                return self._unavailable("environment_unavailable")
            secrets = self._environment_secrets(environment)

            result = self._command("logs", "--tail", str(bounded_tail), "--timestamps", container_ids[0])
            if result.returncode != 0:
                return self._unavailable("docker_query_failed")
            raw = (result.stdout or "")[:MAX_LOG_OUTPUT_CHARS]
            if _CONTENT_MARKER.search(raw):
                lines = ["[redacted content-bearing log output]"]
            else:
                lines = self._sanitize_lines(raw, secrets, bounded_tail)
            return {"availability": "available", "lines": lines, "error_category": None}
        except subprocess.TimeoutExpired:
            return self._unavailable("command_timeout")
        except FileNotFoundError:
            return self._unavailable("docker_unavailable")
        except (OSError, subprocess.SubprocessError):
            return self._unavailable("docker_unavailable")
        except (ValueError, TypeError, json.JSONDecodeError):
            return self._unavailable("invalid_docker_response")

    @staticmethod
    def _environment_secrets(environment: list[str]) -> list[str]:
        values: set[str] = set()
        for entry in environment:
            name, separator, value = entry.partition("=")
            if separator and _SECRET_ENV_NAME.search(name) and value:
                values.add(value)
        return sorted(values, key=len, reverse=True)

    @staticmethod
    def _sanitize_lines(raw: str, secrets: list[str], tail: int) -> list[str]:
        lines = raw.splitlines()[-tail:]
        clean: list[str] = []
        for line in lines:
            value = line[:MAX_LOG_LINE_CHARS]
            for secret in secrets:
                value = value.replace(secret, "[redacted]")
            value = _ASSIGNMENT_SECRET.sub(r"\1\2[redacted]", value)
            value = _BEARER_SECRET.sub("Bearer [redacted]", value)
            value = _URL_CREDENTIAL.sub(r"\1[redacted]@", value)
            value = _WINDOWS_PATH.sub("[path redacted]", value)
            value = _UNIX_PATH.sub("[path redacted]", value)
            clean.append(value)
        return clean

    @staticmethod
    def _unavailable(category: str) -> dict[str, Any]:
        return {"availability": "unavailable", "lines": [], "error_category": category}
