from __future__ import annotations

import pytest
import yaml
from conftest import load_dashboard_module


def test_speech_profile_registry_exposes_pinned_danish_cpu_services():
    profiles_module = load_dashboard_module("profiles")

    profiles = profiles_module.load_speech_profiles()

    assert [profile["id"] for profile in profiles] == ["danish-speech-cpu"]
    profile = profiles[0]
    assert profile["language"] == "Danish (da-DK)"
    assert profile["stt_alias"] == "ddu-stt"
    assert profile["stt_model_id"] == "Systran/faster-whisper-small"
    assert profile["stt_model_revision"] == "536b0662742c02347bc0e980a01041f333bce120"
    assert profile["tts_alias"] == "ddu-tts"
    assert profile["tts_voice_id"] == "da_DK-talesyntese-medium"
    assert profile["tts_model_revision"] == "320d5f7f7751a17ef6512d5c23863056c6a11c0f"
    assert profile["cpu_only"] is True
    assert profile["validation_state"] == "implementation-unverified"


def test_speech_profile_registry_rejects_unreviewed_aliases_and_duplicate_ids(tmp_path):
    profiles_module = load_dashboard_module("profiles")
    source = profiles_module.DEFAULT_PROFILES_PATH
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    document["speech_profiles"].append(dict(document["speech_profiles"][0]))
    registry = tmp_path / "profiles.yaml"
    registry.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate ID"):
        profiles_module.load_speech_profiles(registry)

    document["speech_profiles"] = [dict(document["speech_profiles"][0])]
    document["speech_profiles"][0]["stt_alias"] = "unreviewed-stt"
    registry.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported service alias"):
        profiles_module.load_speech_profiles(registry)

    document["speech_profiles"][0]["stt_alias"] = "ddu-tts"
    document["speech_profiles"][0]["tts_alias"] = "ddu-stt"
    registry.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported service alias"):
        profiles_module.load_speech_profiles(registry)
