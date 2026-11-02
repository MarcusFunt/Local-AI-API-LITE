from __future__ import annotations

import pytest
import yaml
from conftest import load_dashboard_module


def test_speech_profile_registry_exposes_pinned_danish_and_english_cpu_services():
    profiles_module = load_dashboard_module("profiles")

    profiles = profiles_module.load_speech_profiles()

    assert [profile["id"] for profile in profiles] == ["danish-speech-cpu"]
    profile = profiles[0]
    assert profile["name"] == "Danish + English speech / CPU"
    assert profile["language"] == "Danish (da) / English (en)"
    assert profile["stt_alias"] == "ddu-stt"
    assert profile["stt_model_id"] == "Systran/faster-whisper-small"
    assert profile["stt_model_revision"] == "536b0662742c02347bc0e980a01041f333bce120"
    assert profile["tts_alias"] == "ddu-tts"
    assert profile["tts_voice_id"] == "da: da_DK-talesyntese-medium; en: en_US-ljspeech-medium"
    assert profile["tts_model_revision"] == "375a0fe641dea077c2a47b4e9a056d6da521eed3"
    assert profile["tts_runtime_license"] == "GPL-3.0-or-later"
    assert profile["tts_voice_dataset_license"] == "Danish talesyntese: CC0; English LJ Speech: public domain (per pinned voice model cards)"
    assert "Danish and English" in profile["description"]
    assert profile["compose_project"] == "local-ai-api-lite-speech"
    assert profile["compose_profile"] == "danish-speech"
    assert (profile["stt_cpu_limit"], profile["stt_memory_limit"]) == (4.0, "4g")
    assert (profile["tts_cpu_limit"], profile["tts_memory_limit"]) == (1.0, "1g")
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
