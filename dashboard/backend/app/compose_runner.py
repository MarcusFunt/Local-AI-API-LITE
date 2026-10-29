from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from .configuration import (
    CACHE_DESTINATION,
    IMAGE_REF,
    NETWORK_NAME,
    PROJECT_NAME,
    SERVICE_NAME,
    ConfigurationError,
    validate_compose_root,
)

_CONTAINER_ID = re.compile(r"^[0-9a-fA-F]{12,64}$")


class ComposeRunnerError(RuntimeError):
    """Raised when fixed Compose/Docker commands fail validation or deadlines."""


class ComposeRunner:
    """Run only fixed, project-scoped Docker Compose operations."""

    def __init__(
        self,
        project_root: Path,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout: float = 150,
        docker_path: str = "docker",
    ) -> None:
        try:
            self.project_root = validate_compose_root(project_root)
        except ConfigurationError as exc:
            raise ComposeRunnerError(str(exc)) from exc
        self.run = run
        self.timeout = timeout
        self.docker_path = docker_path

    def _compose_args(self, *, env_file: Path | None = None) -> list[str]:
        return [
            self.docker_path,
            "compose",
            "--project-name",
            PROJECT_NAME,
            "--project-directory",
            str(self.project_root),
            "--file",
            str(self.project_root / "compose.yaml"),
            "--env-file",
            str(env_file or self.project_root / ".env"),
        ]

    def _execute(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            result = self.run(
                args,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                shell=False,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ComposeRunnerError(f"Docker command failed: {type(exc).__name__}") from None
        if result.returncode != 0:
            raise ComposeRunnerError("Docker Compose rejected the configured project")
        return result

    def validate_configuration(self, env_file: Path | None = None) -> dict:
        args = self._compose_args(env_file=env_file) + ["config", "--format", "json"]
        result = self._execute(args)
        try:
            rendered = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError):
            raise ComposeRunnerError("Docker Compose returned invalid configuration") from None
        return self._validate_rendered_configuration(rendered)

    @staticmethod
    def _validate_rendered_configuration(rendered: object) -> dict:
        if not isinstance(rendered, dict) or rendered.get("name") != PROJECT_NAME:
            raise ComposeRunnerError("Rendered Compose project identity does not match")
        services = rendered.get("services")
        if not isinstance(services, dict) or set(services) != {SERVICE_NAME}:
            raise ComposeRunnerError("Rendered Compose project has an unexpected service set")
        service = services[SERVICE_NAME]
        if not isinstance(service, dict) or service.get("image") != IMAGE_REF:
            raise ComposeRunnerError("Rendered Compose service image does not match")
        if service.get("ports"):
            raise ComposeRunnerError("Rendered Compose service must not publish host ports")
        networks = service.get("networks")
        inference = networks.get("inference") if isinstance(networks, dict) else None
        aliases = inference.get("aliases") if isinstance(inference, dict) else None
        if not isinstance(aliases, list) or "ddu-llm" not in aliases:
            raise ComposeRunnerError("Rendered Compose service is missing the inference alias")
        rendered_networks = rendered.get("networks")
        project_network = (
            rendered_networks.get("inference") if isinstance(rendered_networks, dict) else None
        )
        if (
            not isinstance(project_network, dict)
            or project_network.get("name") != NETWORK_NAME
            or project_network.get("external") is not True
        ):
            raise ComposeRunnerError("Rendered Compose project is not on the expected external network")
        volumes = service.get("volumes")
        if not isinstance(volumes, list):
            raise ComposeRunnerError("Rendered Compose model-cache mount is missing")
        cache_mounts = [
            volume
            for volume in volumes
            if isinstance(volume, dict)
            and volume.get("type") == "bind"
            and volume.get("target") == CACHE_DESTINATION
        ]
        if len(cache_mounts) != 1:
            raise ComposeRunnerError("Rendered Compose model-cache bind mount does not match")
        source = cache_mounts[0].get("source")
        if not isinstance(source, str) or not Path(source).is_absolute():
            raise ComposeRunnerError("Rendered Compose model-cache source must be absolute")
        return {
            "name": PROJECT_NAME,
            "service": SERVICE_NAME,
            "network": NETWORK_NAME,
            "cache_source": str(Path(source).resolve()),
        }

    def inspect_project(self) -> dict[str, str]:
        expected = self.validate_configuration()
        args = self._compose_args() + ["ps", "--all", "--quiet", SERVICE_NAME]
        result = self._execute(args)
        ids = [item.strip() for item in result.stdout.splitlines() if item.strip()]
        if not ids:
            return {
                "project_name": PROJECT_NAME,
                "service": SERVICE_NAME,
                "runtime_state": "absent",
                "network": NETWORK_NAME,
            }
        if len(ids) != 1 or not _CONTAINER_ID.fullmatch(ids[0]):
            raise ComposeRunnerError("Compose returned an invalid or non-singleton container identity")
        inspected = self._execute([self.docker_path, "inspect", ids[0]])
        try:
            records = json.loads(inspected.stdout)
        except (TypeError, json.JSONDecodeError):
            raise ComposeRunnerError("Docker returned invalid runtime inspection data") from None
        if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
            raise ComposeRunnerError("Docker runtime inspection was not a single container")
        record = records[0]
        config = record.get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        if (
            not isinstance(labels, dict)
            or
            labels.get("com.docker.compose.project") != PROJECT_NAME
            or labels.get("com.docker.compose.service") != SERVICE_NAME
        ):
            raise ComposeRunnerError("Runtime container labels do not match the configured project")
        if config.get("Image") != IMAGE_REF:
            raise ComposeRunnerError("Runtime container image does not match the pinned image")
        network_settings = record.get("NetworkSettings")
        networks = network_settings.get("Networks") if isinstance(network_settings, dict) else None
        network = networks.get(NETWORK_NAME) if isinstance(networks, dict) else None
        aliases = network.get("Aliases") if isinstance(network, dict) else None
        if not isinstance(aliases, list) or "ddu-llm" not in aliases:
            raise ComposeRunnerError("Runtime container is not on the expected network and alias")
        ports = network_settings.get("Ports") if isinstance(network_settings, dict) else None
        if isinstance(ports, dict) and any(bindings for bindings in ports.values()):
            raise ComposeRunnerError("Runtime container has a published host port")
        mounts = record.get("Mounts", [])
        if not isinstance(mounts, list):
            raise ComposeRunnerError("Runtime container mounts are unavailable")
        matching_mounts = [
            mount
            for mount in mounts
            if isinstance(mount, dict)
            and mount.get("Type") == "bind"
            and mount.get("Destination") == CACHE_DESTINATION
            and isinstance(mount.get("Source"), str)
            and str(Path(mount["Source"]).resolve()) == expected["cache_source"]
        ]
        if len(matching_mounts) != 1:
            raise ComposeRunnerError("Runtime container model-cache mount does not match")
        state = record.get("State")
        status = state.get("Status") if isinstance(state, dict) else None
        if status not in {"created", "restarting", "running", "removing", "paused", "exited", "dead"}:
            raise ComposeRunnerError("Runtime container state is unavailable")
        return {
            "project_name": PROJECT_NAME,
            "service": SERVICE_NAME,
            "runtime_state": status,
            "network": NETWORK_NAME,
        }

    def stop_vllm(self) -> dict[str, str]:
        before = self.inspect_project()
        self._execute(self._compose_args() + ["stop", SERVICE_NAME])
        return {**before, "action": "stop"}

    def start_vllm(self) -> dict[str, str]:
        self.inspect_project()
        self._execute(self._compose_args() + ["up", "--detach", "--no-deps", SERVICE_NAME])
        return {
            "project_name": PROJECT_NAME,
            "service": SERVICE_NAME,
            "action": "start",
        }
