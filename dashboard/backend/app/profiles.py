from __future__ import annotations

from math import isclose
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILES_PATH = ROOT / "dashboard" / "config" / "profiles.yaml"
_MATCH_FIELDS = (
    "model_id",
    "model_revision",
    "model_alias",
    "reasoning_parser",
    "engine",
    "image_ref",
    "quantization",
    "max_model_len",
    "max_num_seqs",
)
_REQUIRED_FIELDS = (*_MATCH_FIELDS, "gpu_memory_utilization")
_SPEECH_REQUIRED_FIELDS = (
    "name",
    "language",
    "stt_alias",
    "stt_model_id",
    "stt_model_revision",
    "stt_runtime",
    "stt_license",
    "tts_alias",
    "tts_model_id",
    "tts_model_revision",
    "tts_voice_id",
    "tts_runtime",
    "tts_runtime_license",
    "tts_voice_dataset_license",
    "compose_project",
    "compose_profile",
    "cpu_only",
    "stt_cpu_limit",
    "stt_memory_limit",
    "tts_cpu_limit",
    "tts_memory_limit",
    "max_audio_seconds",
    "max_audio_bytes",
    "max_text_characters",
    "validation_state",
    "description",
)
_SPEECH_PUBLIC_FIELDS = ("id", *_SPEECH_REQUIRED_FIELDS)


def load_profiles(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the versioned, repository-owned profile registry."""
    registry_path = path or DEFAULT_PROFILES_PATH
    document = _load_registry(registry_path)
    profiles = document.get("profiles")
    if not isinstance(profiles, list):
        raise TypeError("The dashboard profile registry must contain a profile list")

    ids: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise TypeError("The dashboard profile registry contains an invalid entry")
        profile_id = profile.get("id")
        if not isinstance(profile_id, str) or not profile_id or profile_id in ids:
            raise ValueError("The dashboard profile registry contains an invalid or duplicate ID")
        ids.add(profile_id)
        if profile.get("validation_state") != "validated":
            raise ValueError(f"Dashboard profile {profile_id!r} is not validated")
        for key in _REQUIRED_FIELDS:
            if key not in profile:
                raise ValueError(f"Dashboard profile {profile_id!r} is missing {key}")
    return profiles


def _load_registry(registry_path: Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError("The dashboard profile registry is unavailable or invalid") from exc
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("The dashboard profile registry has an unsupported format")
    return document


def load_speech_profiles(path: Path | None = None) -> list[dict[str, Any]]:
    """Read fixed speech service profiles without changing narrator matching."""
    registry_path = path or DEFAULT_PROFILES_PATH
    document = _load_registry(registry_path)
    profiles = document.get("speech_profiles")
    if not isinstance(profiles, list):
        raise TypeError("The dashboard profile registry must contain a speech profile list")

    narrator_ids = {profile["id"] for profile in load_profiles(registry_path)}
    ids: set[str] = set()
    for profile in profiles:
        if not isinstance(profile, dict):
            raise TypeError("The dashboard profile registry contains an invalid speech entry")
        profile_id = profile.get("id")
        if not isinstance(profile_id, str) or not profile_id or profile_id in ids or profile_id in narrator_ids:
            raise ValueError("The dashboard speech profile registry contains an invalid or duplicate ID")
        ids.add(profile_id)
        for key in _SPEECH_REQUIRED_FIELDS:
            if key not in profile:
                raise ValueError(f"Dashboard speech profile {profile_id!r} is missing {key}")
        if (
            profile.get("stt_alias") != "ddu-stt"
            or profile.get("tts_alias") != "ddu-tts"
        ):
            raise ValueError(f"Dashboard speech profile {profile_id!r} has an unsupported service alias")
        for key in ("stt_model_revision", "tts_model_revision"):
            revision = profile.get(key)
            if not isinstance(revision, str) or len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
                raise ValueError(f"Dashboard speech profile {profile_id!r} has an invalid {key}")
        if profile.get("cpu_only") is not True:
            raise ValueError(f"Dashboard speech profile {profile_id!r} must be CPU-only")
        if profile.get("validation_state") != "implementation-unverified":
            raise ValueError(f"Dashboard speech profile {profile_id!r} has an unsupported validation state")
    return profiles


def match_profile(configuration: dict[str, Any] | None, profiles: list[dict[str, Any]]) -> str | None:
    """Return an exact validated-profile match for the observed runtime settings."""
    if not configuration:
        return None
    for profile in profiles:
        if not all(configuration.get(key) == profile.get(key) for key in _MATCH_FIELDS):
            continue
        try:
            actual_utilization = float(configuration["gpu_memory_utilization"])
            expected_utilization = float(profile["gpu_memory_utilization"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if isclose(actual_utilization, expected_utilization, rel_tol=0, abs_tol=1e-6):
            return str(profile["id"])
    return None


def public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Return only fields intended for the local dashboard UI."""
    keys = (
        "id",
        "name",
        "model_id",
        "model_revision",
        "model_alias",
        "reasoning_parser",
        "engine",
        "image_ref",
        "quantization",
        "max_model_len",
        "max_num_seqs",
        "gpu_memory_utilization",
        "validation_state",
        "description",
    )
    return {key: profile[key] for key in keys if key in profile}


def public_speech_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Return the pinned speech profile fields intended for the local dashboard UI."""
    return {key: profile[key] for key in _SPEECH_PUBLIC_FIELDS if key in profile}
