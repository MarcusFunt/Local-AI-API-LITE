from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def read_speech_compose() -> dict:
    path = ROOT / "deploy/speech/compose.yaml"
    assert path.is_file(), "the independent speech Compose project must be implemented"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_speech_workers_are_private_cpu_only_and_have_fixed_network_aliases():
    compose = read_speech_compose()
    assert compose["name"] == "local-ai-api-lite-speech"
    assert compose["networks"]["inference"] == {"name": "ddu_inference_bus", "external": True}
    assert compose["services"]["stt"]["profiles"] == ["danish-speech"]
    assert compose["services"]["tts"]["profiles"] == ["danish-speech"]
    assert compose["services"]["stt"]["networks"]["inference"]["aliases"] == ["ddu-stt"]
    assert compose["services"]["tts"]["networks"]["inference"]["aliases"] == ["ddu-tts"]
    for name in ("stt", "tts"):
        service = compose["services"][name]
        assert "ports" not in service
        assert "gpus" not in service
        assert "depends_on" not in service
        assert service["read_only"] is True
        assert service["deploy"]["resources"]["limits"]["cpus"]
        assert service["deploy"]["resources"]["limits"]["memory"]
        cache_mount = next(mount for mount in service["volumes"] if mount["target"] == "/models")
        assert cache_mount["read_only"] is True


def test_speech_installer_requires_an_explicit_write_profile_and_download_action():
    compose = read_speech_compose()
    installer = compose["services"]["model-installer"]

    assert installer["profiles"] == ["install"]
    assert installer["command"] == ["--plan"]
    assert "networks" not in installer
    cache_mount = next(mount for mount in installer["volumes"] if mount["target"] == "/models")
    assert cache_mount["read_only"] is False
    assert installer["user"] == "${MODEL_CACHE_UID:-10001}:${MODEL_CACHE_GID:-10001}"


def test_speech_runtime_dependencies_are_hash_locked_and_python_base_is_pinned():
    lock = (ROOT / "services/speech/requirements.lock").read_text(encoding="utf-8")
    for package, version in (("faster-whisper", "1.2.1"), ("piper-tts", "1.8.0")):
        assert f"{package}=={version} " + "\\" in lock
    package_indexes = [
        index for index, line in enumerate(lock.splitlines())
        if line.endswith("\\") and re.match(r"^[A-Za-z0-9_.-]+==", line)
    ]
    lines = lock.splitlines()
    assert package_indexes
    for position, start in enumerate(package_indexes):
        end = package_indexes[position + 1] if position + 1 < len(package_indexes) else len(lines)
        assert "--hash=sha256:" in "\n".join(lines[start:end]), lines[start]
    dockerfile = (ROOT / "services/speech/Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"(?m)^FROM python:3\.11\.18-slim-bookworm@sha256:[a-f0-9]{64}$", dockerfile)


def test_speech_profile_and_worker_constants_share_the_same_pinned_artifacts():
    import sys

    sys.path.insert(0, str(ROOT / "services/speech"))
    from speech_worker import contracts

    registry = yaml.safe_load((ROOT / "dashboard/config/profiles.yaml").read_text(encoding="utf-8"))
    profile = registry["speech_profiles"][0]
    assert profile["stt_alias"] == contracts.STT_ALIAS
    assert profile["stt_model_id"] == contracts.STT_MODEL_ID
    assert profile["stt_model_revision"] == contracts.STT_MODEL_REVISION
    assert profile["tts_alias"] == contracts.TTS_ALIAS
    assert profile["tts_model_id"] == contracts.TTS_MODEL_ID
    assert profile["tts_model_revision"] == contracts.TTS_MODEL_REVISION
    assert profile["tts_voice_id"] == contracts.TTS_VOICE_ID
