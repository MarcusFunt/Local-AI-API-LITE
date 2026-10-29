from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

import yaml

from .paths import RuntimePathError, data_directory, ensure_private_directory, ensure_private_file
from .profiles import load_profiles

PROJECT_NAME = "local-ai-api-lite"
SERVICE_NAME = "vllm"
NETWORK_NAME = "ddu_inference_bus"
CACHE_DESTINATION = "/root/.cache/huggingface"
IMAGE_REF = (
    "vllm/vllm-openai:v0.32.0@sha256:"
    "befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34"
)
_PROFILE_VALUES: dict[str, dict[str, object]] = {
    "baseline-4k-2": {
        "model_id": "RedHatAI/Qwen3.5-4B-quantized.w4a16",
        "model_revision": "7a613872f394578b0b52b683ff4ac47516b4bcaf",
        "model_alias": "ddu-narrator",
        "reasoning_parser": "qwen3",
        "max_model_len": 4096,
        "max_num_seqs": 2,
        "gpu_memory_utilization": 0.75,
    },
    "baseline-8k-4": {
        "model_id": "RedHatAI/Qwen3.5-4B-quantized.w4a16",
        "model_revision": "7a613872f394578b0b52b683ff4ac47516b4bcaf",
        "model_alias": "ddu-narrator",
        "reasoning_parser": "qwen3",
        "max_model_len": 8192,
        "max_num_seqs": 4,
        "gpu_memory_utilization": 0.75,
    },
}
_ENV_PROFILE_KEYS = {
    "MODEL_ID": "model_id",
    "MODEL_REVISION": "model_revision",
    "MODEL_ALIAS": "model_alias",
    "MAX_MODEL_LEN": "max_model_len",
    "MAX_NUM_SEQS": "max_num_seqs",
    "GPU_MEMORY_UTILIZATION": "gpu_memory_utilization",
    "REASONING_PARSER": "reasoning_parser",
}
_ENV_LINE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$")


class ConfigurationError(RuntimeError):
    """Raised when a local Compose project or profile is not trusted."""


def validate_compose_root(project_root: Path) -> Path:
    """Validate the repository-owned Compose project without contacting Docker."""
    try:
        root = project_root.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ConfigurationError("Compose project root is not a directory")
        compose_file = root / "compose.yaml"
        env_file = root / ".env"
        if not compose_file.is_file() or not env_file.is_file():
            raise ConfigurationError("Compose project must contain compose.yaml and .env")
        document = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, yaml.YAMLError) as exc:
        raise ConfigurationError("Compose project files are unavailable or invalid") from exc

    if not isinstance(document, dict) or document.get("name") != PROJECT_NAME:
        raise ConfigurationError("Compose project identity does not match the dashboard")
    services = document.get("services")
    if not isinstance(services, dict) or set(services) != {SERVICE_NAME}:
        raise ConfigurationError("Compose project must contain only the vLLM service")
    service = services[SERVICE_NAME]
    if not isinstance(service, dict) or service.get("image") != IMAGE_REF:
        raise ConfigurationError("Compose service image does not match the pinned vLLM image")
    if service.get("ports"):
        raise ConfigurationError("Compose service must not publish host ports")
    networks = service.get("networks")
    if not isinstance(networks, dict) or set(networks) != {"inference"}:
        raise ConfigurationError("Compose service must use the inference network")
    network_options = networks["inference"]
    aliases = network_options.get("aliases") if isinstance(network_options, dict) else None
    if not isinstance(aliases, list) or "ddu-llm" not in aliases:
        raise ConfigurationError("Compose service must retain the ddu-llm network alias")
    project_networks = document.get("networks")
    if not isinstance(project_networks, dict) or set(project_networks) != {"inference"}:
        raise ConfigurationError("Compose project must use the shared inference network")
    project_network = project_networks["inference"]
    if (
        not isinstance(project_network, dict)
        or project_network.get("external") is not True
        or project_network.get("name") != NETWORK_NAME
    ):
        raise ConfigurationError("Compose project must use the expected external network")
    volumes = service.get("volumes")
    if not isinstance(volumes, list) or len(volumes) != 1:
        raise ConfigurationError("Compose service must have exactly one model-cache mount")
    volume = volumes[0]
    if isinstance(volume, dict):
        valid_cache_mount = (
            volume.get("type") == "bind" and volume.get("target") == CACHE_DESTINATION
        )
    elif isinstance(volume, str):
        valid_cache_mount = CACHE_DESTINATION in volume and ":" in volume
    else:
        valid_cache_mount = False
    if not valid_cache_mount:
        raise ConfigurationError("Compose service model-cache mount is not the expected bind mount")
    return root


class LocalSettingsStore:
    """Protected per-user settings; no project root is inferred by default."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_directory() / "settings.json"

    def load_compose_root(self) -> Path | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            configured = raw.get("compose_root") if isinstance(raw, dict) else None
            if (
                not isinstance(raw, dict)
                or raw.get("version") != 1
                or not isinstance(configured, str)
                or not configured
                or not Path(configured).is_absolute()
            ):
                return None
            root = validate_compose_root(Path(configured))
            return root
        except (OSError, ValueError, TypeError, ConfigurationError):
            return None

    def controls_available(self) -> bool:
        return self.load_compose_root() is not None

    def configure_compose_root(
        self,
        project_root: Path,
        validate_configuration: Callable[[Path], object] | None = None,
    ) -> Path:
        root = validate_compose_root(project_root)
        if validate_configuration is None:
            from .compose_runner import ComposeRunner, ComposeRunnerError

            try:
                ComposeRunner(root).validate_configuration()
            except ComposeRunnerError as exc:
                raise ConfigurationError(str(exc)) from exc
        else:
            validate_configuration(root)
        self._write_settings(root)
        return root

    def _write_settings(self, root: Path) -> None:
        temporary: Path | None = None
        try:
            ensure_private_directory(self.path.parent)
            descriptor, name = tempfile.mkstemp(prefix=".settings.", suffix=".tmp", dir=self.path.parent)
            temporary = Path(name)
            os.close(descriptor)
            ensure_private_file(temporary)
            temporary.write_text(
                json.dumps({"version": 1, "compose_root": str(root)}, indent=2) + "\n",
                encoding="utf-8",
            )
            ensure_private_file(temporary)
            os.replace(temporary, self.path)
            ensure_private_file(self.path)
        except (OSError, RuntimePathError) as exc:
            raise ConfigurationError("Cannot securely save local dashboard settings") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def update_profile_environment(
    project_root: Path,
    profile_id: str,
    validate_configuration: Callable[[Path], object] | None = None,
) -> None:
    """Atomically update only known profile keys after validating the candidate Compose file."""
    root = validate_compose_root(project_root)
    expected = _PROFILE_VALUES.get(profile_id)
    if expected is None:
        raise ConfigurationError("Requested dashboard profile is not allowlisted")
    try:
        profiles = load_profiles()
        profile = next((item for item in profiles if item.get("id") == profile_id), None)
    except (OSError, TypeError, ValueError) as exc:
        raise ConfigurationError("Dashboard profile registry is unavailable") from exc
    if profile is None or profile.get("validation_state") != "validated":
        raise ConfigurationError("Requested dashboard profile is not validated")
    for field, value in expected.items():
        if profile.get(field) != value:
            raise ConfigurationError("Dashboard profile differs from the pinned allowlist")

    env_path = root / ".env"
    temporary: Path | None = None
    try:
        ensure_private_file(env_path)
        original = env_path.read_text(encoding="utf-8")
        newline = "\r\n" if "\r\n" in original else "\n"
        had_final_newline = original.endswith(("\n", "\r"))
        lines = original.splitlines()
        seen: set[str] = set()
        output: list[str] = []
        for line in lines:
            match = _ENV_LINE.match(line)
            if match is None or match.group(2) not in _ENV_PROFILE_KEYS:
                output.append(line)
                continue
            key = match.group(2)
            if key in seen:
                raise ConfigurationError(f"Compose environment contains duplicate {key}")
            seen.add(key)
            field = _ENV_PROFILE_KEYS[key]
            comment = re.search(r"\s+#.*$", match.group(4))
            suffix = comment.group(0) if comment else ""
            output.append(f"{match.group(1)}{key}{match.group(3)}{expected[field]}{suffix}")
        for key, field in _ENV_PROFILE_KEYS.items():
            if key not in seen:
                output.append(f"{key}={expected[field]}")
        candidate = newline.join(output) + (newline if had_final_newline else "")

        descriptor, name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=root)
        temporary = Path(name)
        os.close(descriptor)
        ensure_private_file(temporary)
        temporary.write_text(candidate, encoding="utf-8", newline="")
        ensure_private_file(temporary)
        if validate_configuration is None:
            from .compose_runner import ComposeRunner, ComposeRunnerError

            try:
                ComposeRunner(root).validate_configuration(env_file=temporary)
            except ComposeRunnerError as exc:
                raise ConfigurationError(str(exc)) from exc
        else:
            validate_configuration(temporary)
        os.replace(temporary, env_path)
        ensure_private_file(env_path)
    except ConfigurationError:
        raise
    except (OSError, RuntimePathError, UnicodeError) as exc:
        raise ConfigurationError("Cannot securely update the Compose environment") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
