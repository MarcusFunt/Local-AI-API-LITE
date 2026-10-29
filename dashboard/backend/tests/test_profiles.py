from __future__ import annotations

from conftest import load_dashboard_module


def test_profiles_are_versioned_and_runtime_configuration_matches_the_real_profile():
    profiles_module = load_dashboard_module("profiles")
    profiles = profiles_module.load_profiles()

    assert [profile["id"] for profile in profiles] == ["baseline-4k-2", "baseline-8k-4"]
    assert profiles[1]["reasoning_parser"] == "qwen3"
    runtime_configuration = {
        "model_id": "RedHatAI/Qwen3.5-4B-quantized.w4a16",
        "model_revision": "7a613872f394578b0b52b683ff4ac47516b4bcaf",
        "model_alias": "ddu-narrator",
        "reasoning_parser": "qwen3",
        "engine": "vLLM 0.32.0",
        "image_ref": "vllm/vllm-openai:v0.32.0@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34",
        "quantization": "W4A16",
        "max_model_len": 8192,
        "max_num_seqs": 4,
        "gpu_memory_utilization": 0.75,
    }

    assert profiles_module.match_profile(runtime_configuration, profiles) == "baseline-8k-4"


def test_changed_runtime_configuration_does_not_match_a_validated_profile():
    profiles_module = load_dashboard_module("profiles")
    profiles = profiles_module.load_profiles()
    runtime_configuration = {
        "model_id": "unreviewed/model",
        "model_revision": "unknown",
        "model_alias": "ddu-narrator",
        "max_model_len": 8192,
        "max_num_seqs": 4,
        "gpu_memory_utilization": 0.75,
    }

    assert profiles_module.match_profile(runtime_configuration, profiles) is None


def test_changed_reasoning_parser_does_not_match_a_validated_profile():
    profiles_module = load_dashboard_module("profiles")
    profiles = profiles_module.load_profiles()
    runtime_configuration = {
        "model_id": "RedHatAI/Qwen3.5-4B-quantized.w4a16",
        "model_revision": "7a613872f394578b0b52b683ff4ac47516b4bcaf",
        "model_alias": "ddu-narrator",
        "engine": "vLLM 0.32.0",
        "image_ref": "vllm/vllm-openai:v0.32.0@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34",
        "quantization": "W4A16",
        "max_model_len": 8192,
        "max_num_seqs": 4,
        "gpu_memory_utilization": 0.75,
        "reasoning_parser": "deepseek_r1",
    }

    assert profiles_module.match_profile(runtime_configuration, profiles) is None


def test_missing_reasoning_parser_does_not_match_a_validated_profile():
    profiles_module = load_dashboard_module("profiles")
    profiles = profiles_module.load_profiles()
    runtime_configuration = {
        "model_id": "RedHatAI/Qwen3.5-4B-quantized.w4a16",
        "model_revision": "7a613872f394578b0b52b683ff4ac47516b4bcaf",
        "model_alias": "ddu-narrator",
        "engine": "vLLM 0.32.0",
        "image_ref": "vllm/vllm-openai:v0.32.0@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34",
        "quantization": "W4A16",
        "max_model_len": 8192,
        "max_num_seqs": 4,
        "gpu_memory_utilization": 0.75,
    }

    assert profiles_module.match_profile(runtime_configuration, profiles) is None


def test_different_engine_or_image_does_not_match_a_validated_profile():
    profiles_module = load_dashboard_module("profiles")
    profiles = profiles_module.load_profiles()
    runtime_configuration = {
        "model_id": "RedHatAI/Qwen3.5-4B-quantized.w4a16",
        "model_revision": "7a613872f394578b0b52b683ff4ac47516b4bcaf",
        "model_alias": "ddu-narrator",
        "reasoning_parser": "qwen3",
        "engine": "vLLM 0.32.0",
        "image_ref": "vllm/vllm-openai:v0.32.0@sha256:" + "0" * 64,
        "quantization": "W4A16",
        "max_model_len": 8192,
        "max_num_seqs": 4,
        "gpu_memory_utilization": 0.75,
    }

    assert profiles_module.match_profile(runtime_configuration, profiles) is None


def test_incomplete_runtime_configuration_is_unrecognized_without_raising():
    profiles_module = load_dashboard_module("profiles")
    profiles = profiles_module.load_profiles()
    runtime_configuration = {
        "model_id": "RedHatAI/Qwen3.5-4B-quantized.w4a16",
        "model_revision": "7a613872f394578b0b52b683ff4ac47516b4bcaf",
        "model_alias": "ddu-narrator",
        "max_model_len": 8192,
        "max_num_seqs": 4,
    }
    try:
        result = profiles_module.match_profile(runtime_configuration, profiles)
    except (TypeError, ValueError):
        result = "invalid runtime configuration raised"

    assert result is None
