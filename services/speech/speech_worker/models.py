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
    TTS_VOICE_IDS,
    MissingArtifactError,
    SpeechLanguage,
)

STT_METADATA = {
    "id": "ddu-stt",
    "object": "model",
    "owned_by": "local-ai-api-lite",
    "model_id": STT_MODEL_ID,
    "revision": STT_MODEL_REVISION,
    "languages": ["da", "en"],
    "runtime": "faster-whisper 1.2.1 / CTranslate2 CPU int8",
}
TTS_METADATA = {
    "id": "ddu-tts",
    "object": "model",
    "owned_by": "local-ai-api-lite",
    "model_id": TTS_MODEL_ID,
    "revision": TTS_MODEL_REVISION,
    "languages": ["da", "en"],
    "voice_ids": TTS_VOICE_IDS,
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

    def transcribe(self, audio: Any, language: SpeechLanguage) -> dict[str, Any]:
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_tts_adapter() -> Any:
    base = Path(os.environ.get("PIPER_VOICE_DIR", "/models/speech/piper"))
    paths: dict[SpeechLanguage, tuple[Path, Path]] = {}
    try:
        manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("repository") != TTS_MODEL_ID
            or manifest.get("revision") != TTS_MODEL_REVISION
            or not isinstance(manifest.get("voices"), dict)
        ):
            raise MissingArtifactError()
        for language, voice_id in TTS_VOICE_IDS.items():
            entry = manifest["voices"].get(language)
            voice_path = base / language / "voice.onnx"
            config_path = base / language / "voice.onnx.json"
            if not isinstance(entry, dict) or entry.get("voice_id") != voice_id:
                raise MissingArtifactError()
            voice_config = json.loads(config_path.read_text(encoding="utf-8"))
            audio_config = voice_config.get("audio") if isinstance(voice_config, dict) else None
            sample_rate = audio_config.get("sample_rate") if isinstance(audio_config, dict) else None
            if (
                type(sample_rate) is not int
                or sample_rate <= 0
                or voice_path.stat().st_size <= 0
                or entry.get("files") != {
                    "voice.onnx": _sha256(voice_path),
                    "voice.onnx.json": _sha256(config_path),
                }
            ):
                raise MissingArtifactError()
            paths[language] = voice_path, config_path
    except (OSError, ValueError) as exc:
        raise MissingArtifactError() from exc

    from piper import PiperVoice

    voices = {
        language: PiperVoice.load(str(voice_path), config_path=str(config_path), use_cuda=False)
        for language, (voice_path, config_path) in paths.items()
    }
    return PiperAdapter(voices)


class PiperAdapter:
    def __init__(self, voices: dict[SpeechLanguage, Any]):
        self.voices = voices

    def synthesize_wav(self, text: str, language: SpeechLanguage) -> bytes:
        import io
        import wave

        audio = io.BytesIO()
        with wave.open(audio, "wb") as wav_file:
            self.voices[language].synthesize_wav(text, wav_file)
        return audio.getvalue()
