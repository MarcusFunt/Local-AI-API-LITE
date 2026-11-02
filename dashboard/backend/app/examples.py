from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXAMPLES_PATH = ROOT / "dashboard" / "config" / "examples.yaml"
DEFAULT_COMPOSE_PATH = ROOT / "examples" / "compose.yaml"

ExampleId = Literal["chat-voice"]
ExampleState = Literal["stopped", "starting", "ready", "stopping", "failed", "recovery-required"]


@dataclass(frozen=True, slots=True)
class ExampleDefinition:
    id: ExampleId
    name: str
    description: str
    compose_project: str
    compose_file: Path
    compose_service: str
    host_ip: str
    host_port: int
    container_port: int
    chat_service: str
    chat_model_alias: str
    speech_services: tuple[str, str]
    idle_timeout_seconds: int
    cpu_limit: float
    memory_limit_bytes: int
    pid_limit: int
    tmpfs_limit_bytes: int
    max_audio_bytes: int
    max_audio_seconds: int
    max_tts_characters: int


class ExampleDependencyStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["ready", "stopped", "unavailable", "unknown"] = "unknown"
    active_profile: str | None = None
    phase: str | None = None
    observed_at: str | None = None


class ExamplePublicStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ExampleId = "chat-voice"
    name: str = "Chat + Voice"
    state: ExampleState = "stopped"
    chat_dependency: ExampleDependencyStatus = Field(default_factory=ExampleDependencyStatus)
    voice_dependency: ExampleDependencyStatus = Field(default_factory=ExampleDependencyStatus)
    missing_dependencies: list[str] = Field(default_factory=list)
    started_at: str | None = None
    last_error_category: str | None = None
    idle_expires_at: str | None = None
    activity_available: bool = True


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found duplicate key",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as source:
            return yaml.load(source, Loader=_UniqueKeyLoader)
    except (OSError, TypeError, yaml.YAMLError):
        raise ValueError("The dashboard example definition is unavailable or invalid") from None


def _mapping(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    try:
        valid = isinstance(value, dict) and set(value) == keys
    except TypeError:
        valid = False
    if not valid:
        raise ValueError(f"The dashboard example {label} is invalid")
    return value


def _fixed_int(value: Any, expected: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise ValueError(f"The dashboard example {label} is invalid")
    return value


def _fixed_text(value: Any, expected: str, label: str) -> str:
    if not isinstance(value, str) or value != expected:
        raise ValueError(f"The dashboard example {label} is invalid")
    return value


def load_example_registry(
    path: Path | None = None,
    compose_path: Path | None = None,
) -> dict[ExampleId, ExampleDefinition]:
    """Load the fixed example registry and validate its Compose boundary."""
    registry_path = Path(path) if path is not None else DEFAULT_EXAMPLES_PATH
    document = _mapping(_load_yaml(registry_path), {"version", "examples"}, "registry")
    if (
        isinstance(document["version"], bool)
        or not isinstance(document["version"], int)
        or document["version"] != 1
    ):
        raise ValueError("The dashboard example registry version is unsupported")
    entries = document["examples"]
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError("The dashboard example registry must contain exactly one example")

    raw = _mapping(
        entries[0],
        {"id", "name", "description", "compose", "dependencies", "limits"},
        "entry",
    )
    example_id = _fixed_text(raw["id"], "chat-voice", "ID")
    if example_id != "chat-voice":
        raise ValueError("The dashboard example ID is not supported")
    compose = _mapping(
        raw["compose"],
        {"project", "file", "service", "host_ip", "host_port", "container_port"},
        "Compose settings",
    )
    dependencies = _mapping(raw["dependencies"], {"chat", "voice"}, "dependencies")
    limits = _mapping(
        raw["limits"],
        {
            "idle_timeout_seconds",
            "cpu",
            "memory_bytes",
            "pids",
            "tmpfs_bytes",
            "max_audio_bytes",
            "max_audio_seconds",
            "max_tts_characters",
        },
        "limits",
    )

    if raw["name"] != "Chat + Voice" or not isinstance(raw["description"], str) or not raw["description"].strip():
        raise ValueError("The dashboard example public label is invalid")
    if compose != {
        "project": "local-ai-api-lite-examples",
        "file": "../../examples/compose.yaml",
        "service": "chat-voice",
        "host_ip": "127.0.0.1",
        "host_port": 8766,
        "container_port": 8080,
    }:
        raise ValueError("The dashboard example Compose settings are invalid")
    if dependencies != {"chat": "ddu-narrator", "voice": ["ddu-stt", "ddu-tts"]}:
        raise ValueError("The dashboard example dependencies are invalid")
    expected_limits = {
        "idle_timeout_seconds": 1800,
        "cpu": 1.0,
        "memory_bytes": 1_073_741_824,
        "pids": 128,
        "tmpfs_bytes": 67_108_864,
        "max_audio_bytes": 25_000_000,
        "max_audio_seconds": 120,
        "max_tts_characters": 4000,
    }
    if limits != expected_limits:
        raise ValueError("The dashboard example resource and request limits are invalid")
    if type(limits["cpu"]) is not float or limits["cpu"] != 1.0:
        raise ValueError("The dashboard example CPU limit is invalid")
    for key, expected in expected_limits.items():
        if isinstance(expected, int):
            _fixed_int(limits[key], expected, key)

    entry = ExampleDefinition(
        id="chat-voice",
        name="Chat + Voice",
        description=raw["description"],
        compose_project="local-ai-api-lite-examples",
        compose_file=DEFAULT_COMPOSE_PATH,
        compose_service="chat-voice",
        host_ip="127.0.0.1",
        host_port=8766,
        container_port=8080,
        chat_service="ddu-llm",
        chat_model_alias="ddu-narrator",
        speech_services=("ddu-stt", "ddu-tts"),
        idle_timeout_seconds=1800,
        cpu_limit=1.0,
        memory_limit_bytes=1_073_741_824,
        pid_limit=128,
        tmpfs_limit_bytes=67_108_864,
        max_audio_bytes=25_000_000,
        max_audio_seconds=120,
        max_tts_characters=4000,
    )
    compose_document = _load_yaml(Path(compose_path) if compose_path is not None else entry.compose_file)
    validate_compose_document(compose_document, entry)
    return {entry.id: entry}


def get_example(
    example_id: str,
    path: Path | None = None,
    compose_path: Path | None = None,
) -> ExampleDefinition:
    examples = load_example_registry(path, compose_path)
    try:
        return examples[example_id]  # type: ignore[index]
    except KeyError:
        raise ValueError("The requested example ID is not supported") from None


def validate_compose_document(document: Any, example: ExampleDefinition) -> None:
    """Reject all Compose changes outside the one registered, bounded service."""
    root = _mapping(document, {"name", "services", "networks"}, "Compose document")
    if root["name"] != example.compose_project:
        raise ValueError("The example Compose project name is invalid")
    services = _mapping(root["services"], {example.compose_service}, "Compose services")
    networks = _mapping(root["networks"], {"inference"}, "Compose networks")
    if networks["inference"] != {"name": "ddu_inference_bus", "external": True}:
        raise ValueError("The example Compose network is invalid")

    service = _mapping(
        services[example.compose_service],
        {
            "image",
            "build",
            "environment",
            "ports",
            "networks",
            "user",
            "read_only",
            "tmpfs",
            "cap_drop",
            "security_opt",
            "cpus",
            "mem_limit",
            "pids_limit",
            "init",
            "stop_grace_period",
            "healthcheck",
            "logging",
        },
        "Compose service",
    )
    expected_service_values = {
        "image": "local-ai-api-lite-chat-voice:latest",
        "build": {"context": "./chat-voice", "dockerfile": "Dockerfile"},
        "environment": {"EXAMPLE_UPSTREAM_TOKEN": "${EXAMPLE_UPSTREAM_TOKEN:-}"},
        "ports": [
            {
                "target": example.container_port,
                "published": str(example.host_port),
                "host_ip": example.host_ip,
                "protocol": "tcp",
            }
        ],
        "networks": ["inference"],
        "user": "10001:10001",
        "read_only": True,
        "tmpfs": [f"/tmp:rw,nosuid,nodev,size={example.tmpfs_limit_bytes}"],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": "1g",
        "pids_limit": example.pid_limit,
        "init": True,
        "stop_grace_period": "10s",
        "healthcheck": {
            "test": [
                "CMD",
                "python",
                "-c",
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)",
            ],
            "interval": "10s",
            "timeout": "3s",
            "retries": 3,
            "start_period": "10s",
        },
        "logging": {
            "driver": "json-file",
            "options": {"max-size": "1m", "max-file": "2"},
        },
    }
    for key, expected in expected_service_values.items():
        if service[key] != expected:
            raise ValueError(f"The example Compose service setting {key} is invalid")
    cpus = service["cpus"]
    if isinstance(cpus, bool) or not isinstance(cpus, (float, int)) or cpus != example.cpu_limit:
        raise ValueError("The example Compose CPU limit is invalid")
