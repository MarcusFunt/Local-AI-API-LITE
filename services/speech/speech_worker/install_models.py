from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from .contracts import (
    STT_MODEL_ID,
    STT_MODEL_REVISION,
    TTS_MODEL_ID,
    TTS_MODEL_REVISION,
    TTS_VOICE_IDS,
    SpeechLanguage,
)

VOICE_FILES: dict[SpeechLanguage, dict[str, str]] = {
    "da": {
        "voice.onnx": "da/da_DK/talesyntese/medium/da_DK-talesyntese-medium.onnx",
        "voice.onnx.json": "da/da_DK/talesyntese/medium/da_DK-talesyntese-medium.onnx.json",
    },
    "en": {
        "voice.onnx": "en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx",
        "voice.onnx.json": "en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx.json",
    },
}


def build_plan() -> dict[str, Any]:
    return {
        "stt": {"repository": STT_MODEL_ID, "revision": STT_MODEL_REVISION},
        "tts": {
            "repository": TTS_MODEL_ID,
            "revision": TTS_MODEL_REVISION,
            "voices": {
                language: {"voice_id": TTS_VOICE_IDS[language], "files": dict(files)}
                for language, files in VOICE_FILES.items()
            },
        },
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def install_artifacts(
    model_root: Path,
    *,
    snapshot_download_fn: Any | None = None,
    file_download_fn: Any | None = None,
) -> dict[str, Path]:
    if snapshot_download_fn is None or file_download_fn is None:
        from huggingface_hub import hf_hub_download, snapshot_download

        snapshot_download_fn = snapshot_download_fn or snapshot_download
        file_download_fn = file_download_fn or hf_hub_download

    hub_cache = model_root / "hub"
    snapshot = Path(
        snapshot_download_fn(
            repo_id=STT_MODEL_ID,
            revision=STT_MODEL_REVISION,
            cache_dir=str(hub_cache),
            local_files_only=False,
        )
    )
    if snapshot.name != STT_MODEL_REVISION or not (snapshot / "model.bin").is_file():
        raise RuntimeError("Pinned artifact installation failed: STT snapshot revision mismatch")

    downloaded_voices: dict[SpeechLanguage, dict[str, Path]] = {}
    for language, files in VOICE_FILES.items():
        downloaded_files = {}
        for destination_name, filename in files.items():
            source = Path(
                file_download_fn(
                    repo_id=TTS_MODEL_ID,
                    filename=filename,
                    revision=TTS_MODEL_REVISION,
                    cache_dir=str(hub_cache),
                    local_files_only=False,
                )
            )
            if not source.is_file() or source.stat().st_size <= 0:
                raise RuntimeError("Pinned artifact installation failed: TTS voice file missing or empty")
            downloaded_files[destination_name] = source
        try:
            voice_config = json.loads(downloaded_files["voice.onnx.json"].read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("Pinned artifact installation failed: invalid TTS voice configuration") from exc
        audio_config = voice_config.get("audio") if isinstance(voice_config, dict) else None
        sample_rate = audio_config.get("sample_rate") if isinstance(audio_config, dict) else None
        if type(sample_rate) is not int or sample_rate <= 0:
            raise RuntimeError("Pinned artifact installation failed: unsupported TTS voice configuration")
        downloaded_voices[language] = downloaded_files

    voice_dir = model_root / "speech" / "piper"
    manifest = {
        "repository": TTS_MODEL_ID,
        "revision": TTS_MODEL_REVISION,
        "voices": {},
    }
    for language, files in downloaded_voices.items():
        destination_dir = voice_dir / language
        destination_dir.mkdir(parents=True, exist_ok=True)
        hashes = {}
        for name, source in files.items():
            destination = destination_dir / name
            shutil.copyfile(source, destination)
            hashes[name] = _sha256(destination)
        manifest["voices"][language] = {"voice_id": TTS_VOICE_IDS[language], "files": hashes}
    manifest_path = voice_dir / "manifest.json"
    temporary_manifest = voice_dir / "manifest.json.tmp"
    temporary_manifest.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    temporary_manifest.replace(manifest_path)
    return {"stt_snapshot": snapshot, "voice_dir": voice_dir, "manifest": manifest_path}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Install only the pinned Danish and English speech artifacts.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true", help="Print the pinned artifact plan without writing.")
    action.add_argument("--install", action="store_true", help="Download pinned artifacts into the mounted model cache.")
    args = parser.parse_args(argv)
    for role, artifact in build_plan().items():
        print(f"Artifact ({role}): {artifact['repository']}@{artifact['revision']}")
        for language, voice in artifact.get("voices", {}).items():
            print(f"Voice ({language}): {voice['voice_id']}")
            for name, filename in voice["files"].items():
                print(f"File: {filename} -> {language}/{name}")
    print("Estimated cache allocation: 700 MiB.")
    if args.plan:
        print("Plan only; no files were written.")
        return

    model_root = Path(os.environ.get("MODEL_ROOT", "/models"))
    try:
        install_artifacts(model_root)
    except Exception:  # noqa: BLE001 - HF errors can include local paths and credentials.
        raise SystemExit("Pinned speech artifact installation failed.") from None
    print("Pinned Danish and English speech artifacts installed.")


if __name__ == "__main__":
    main()
