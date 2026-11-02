from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .contracts import (
    STT_MODEL_ID,
    STT_MODEL_REVISION,
    TTS_MODEL_ID,
    TTS_MODEL_REVISION,
    TTS_VOICE_ID,
    MissingArtifactError,
)

STT_METADATA = {
    "id": "ddu-stt",
    "object": "model",
    "owned_by": "local-ai-api-lite",
    "model_id": STT_MODEL_ID,
    "revision": STT_MODEL_REVISION,
    "runtime": "faster-whisper 1.2.1 / CTranslate2 CPU int8",
}
TTS_METADATA = {
    "id": "ddu-tts",
    "object": "model",
    "owned_by": "local-ai-api-lite",
    "model_id": TTS_MODEL_ID,
    "revision": TTS_MODEL_REVISION,
    "voice_id": TTS_VOICE_ID,
    "runtime": "piper-tts 1.8.0 / ONNX Runtime CPU",
}


def _snapshot_path() -> Path:
    from huggingface_hub import snapshot_download

    try:
        path = Path(
            snapshot_download(
                repo_id=STT_MODEL_ID,
                revision=STT_MODEL_REVISION,
                cache_dir=os.environ.get("HF_HUB_CACHE", "/models/hub"),
                local_files_only=True,
            )
        )
    except Exception as exc:
        raise MissingArtifactError() from exc
    if path.name != STT_MODEL_REVISION or not (path / "model.bin").is_file():
        raise MissingArtifactError()
    return path


def create_stt_adapter() -> Any:
    from faster_whisper import WhisperModel

    model = WhisperModel(
        str(_snapshot_path()),
        device="cpu",
        compute_type="int8",
        cpu_threads=int(os.environ.get("STT_CPU_THREADS", "4")),
        num_workers=1,
        local_files_only=True,
    )
    return FasterWhisperAdapter(model)


class FasterWhisperAdapter:
    def __init__(self, model: Any):
        self.model = model

    def transcribe(self, audio: Any, language: str) -> dict[str, Any]:
        segments, info = self.model.transcribe(
            audio,
            language=language,
            task="transcribe",
            beam_size=1,
            vad_filter=True,
            log_progress=False,
        )
        result_segments = [
            {"start": float(segment.start), "end": float(segment.end), "text": str(segment.text)}
            for segment in segments
        ]
        return {
            "text": " ".join(segment["text"].strip() for segment in result_segments).strip(),
            "language": str(info.language),
            "duration": float(info.duration),
            "segments": result_segments,
        }


def _voice_paths() -> tuple[Path, Path, Path]:
    base = Path(os.environ.get("PIPER_VOICE_DIR", "/models/speech/piper"))
    return base / "voice.onnx", base / "voice.onnx.json", base / "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_tts_adapter() -> Any:
    voice_path, config_path, manifest_path = _voice_paths()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        voice_config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MissingArtifactError() from exc
    if (
        manifest.get("repository") != TTS_MODEL_ID
        or manifest.get("revision") != TTS_MODEL_REVISION
        or manifest.get("voice_id") != TTS_VOICE_ID
        or not voice_path.is_file()
        or manifest.get("files") != {
            "voice.onnx": _sha256(voice_path),
            "voice.onnx.json": _sha256(config_path),
        }
        or voice_config.get("audio", {}).get("sample_rate") is None
    ):
        raise MissingArtifactError()

    from piper import PiperVoice

    voice = PiperVoice.load(str(voice_path), config_path=str(config_path), use_cuda=False)
    return PiperAdapter(voice)


class PiperAdapter:
    def __init__(self, voice: Any):
        self.voice = voice

    def synthesize_wav(self, text: str) -> bytes:
        import io
        import wave

        audio = io.BytesIO()
        with wave.open(audio, "wb") as wav_file:
            self.voice.synthesize_wav(text, wav_file)
        return audio.getvalue()
