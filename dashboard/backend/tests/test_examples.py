from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from conftest import load_dashboard_module

ROOT = Path(__file__).resolve().parents[3]


def _registry_module():
    return load_dashboard_module("examples")


def _compose_document() -> dict:
    return yaml.safe_load((ROOT / "examples" / "compose.yaml").read_text(encoding="utf-8"))


def test_registry_exposes_only_the_fixed_chat_voice_example_and_limits():
    module = _registry_module()

    examples = module.load_example_registry()

    assert set(examples) == {"chat-voice"}
    example = examples["chat-voice"]
    assert example.name == "Chat + Voice"
    assert "Danish or English" in example.description
    assert "chat, transcription, and spoken replies" in example.description
    assert "Danish on reload" in example.description
    assert example.compose_project == "local-ai-api-lite-examples"
    assert example.compose_service == "chat-voice"
    assert example.chat_model_alias == "ddu-narrator"
    assert example.chat_service == "ddu-llm"
    assert example.speech_services == ("ddu-stt", "ddu-tts")
    assert example.host_ip == "127.0.0.1"
    assert example.host_port == 8766
    assert example.container_port == 8080
    assert example.idle_timeout_seconds == 1800
    assert example.cpu_limit == 1.0
    assert example.memory_limit_bytes == 1_073_741_824
    assert example.pid_limit == 128
    assert example.tmpfs_limit_bytes == 67_108_864
    assert example.max_audio_bytes == 25_000_000
    assert example.max_audio_seconds == 120
    assert example.max_tts_characters == 4000


def test_compose_has_one_loopback_only_hardened_service_on_the_private_network():
    module = _registry_module()
    example = module.load_example_registry()["chat-voice"]
    document = _compose_document()

    module.validate_compose_document(document, example)

    assert document["name"] == "local-ai-api-lite-examples"
    service = document["services"]["chat-voice"]
    assert service["ports"] == [
        {"target": 8080, "published": "8766", "host_ip": "127.0.0.1", "protocol": "tcp"}
    ]
    assert service["read_only"] is True
    assert service["user"] == "10001:10001"
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["cpus"] == 1.0
    assert service["mem_limit"] == "1g"
    assert service["pids_limit"] == 128
    assert not service.get("volumes")
    assert document["networks"]["inference"] == {"name": "ddu_inference_bus", "external": True}


def test_registry_rejects_an_unknown_example_id(tmp_path):
    module = _registry_module()
    source = ROOT / "dashboard" / "config" / "examples.yaml"
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    document["examples"][0]["id"] = "arbitrary"
    path = tmp_path / "examples.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError):
        module.load_example_registry(path)


def test_registry_rejects_unknown_keys(tmp_path):
    module = _registry_module()
    source = ROOT / "dashboard" / "config" / "examples.yaml"
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    document["examples"][0]["extra_option"] = "unsafe"
    path = tmp_path / "examples.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError):
        module.load_example_registry(path)


def test_registry_rejects_boolean_cpu_limit(tmp_path):
    module = _registry_module()
    source = ROOT / "dashboard" / "config" / "examples.yaml"
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    document["examples"][0]["limits"]["cpu"] = True
    path = tmp_path / "examples.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError):
        module.load_example_registry(path)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda doc: doc["services"].update({"extra": {"image": "unsafe"}}),
        lambda doc: doc["services"]["chat-voice"]["ports"][0].update(host_ip="0.0.0.0"),
        lambda doc: doc["services"]["chat-voice"]["ports"][0].update(target=8766),
        lambda doc: doc["services"]["chat-voice"].update(privileged=True),
        lambda doc: doc["services"]["chat-voice"].update(devices=["/dev/nvidia0:/dev/nvidia0"]),
        lambda doc: doc["services"]["chat-voice"].update(volumes=["/var/run/docker.sock:/var/run/docker.sock"]),
        lambda doc: doc["services"]["chat-voice"].update(restart="unless-stopped"),
        lambda doc: doc["services"]["chat-voice"].update(command=["sh", "-c", "id"]),
        lambda doc: doc["services"]["chat-voice"].update(entrypoint=["sh"]),
        lambda doc: doc["services"]["chat-voice"].update(pids_limit=100000),
        lambda doc: doc["services"]["chat-voice"]["networks"].append("host"),
        lambda doc: doc["networks"]["inference"].update(external=False),
    ],
)
def test_compose_validator_rejects_unsafe_or_unregistered_changes(mutation):
    module = _registry_module()
    example = module.load_example_registry()["chat-voice"]
    document = deepcopy(_compose_document())
    mutation(document)

    with pytest.raises(ValueError):
        module.validate_compose_document(document, example)


def test_dockerfile_uses_a_digest_pinned_python_base_and_only_app_sources():
    dockerfile = (ROOT / "examples" / "chat-voice" / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12.16-slim-bookworm@sha256:" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert " app/ /app/app/" in dockerfile
    assert "requirements.lock" in dockerfile
    assert ".env" not in dockerfile
    assert "model-cache" not in dockerfile
    assert "dashboard" not in dockerfile
