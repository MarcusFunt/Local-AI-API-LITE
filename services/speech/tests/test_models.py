from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from speech_worker.app import create_tts_app
from speech_worker.contracts import MissingArtifactError, TTS_MODEL_REVISION
from speech_worker.models import FasterWhisperAdapter, create_tts_adapter
from starlette.testclient import TestClient


async def _wait_for_initialization(app):
    async def wait():
        while app.state.worker.status == "loading":
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), timeout=2)


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    manifest = {"repository": "rhasspy/piper-voices", "revision": TTS_MODEL_REVISION, "voices": {}}
    for language, voice_id in [("da", "da_DK-talesyntese-medium"), ("en", "en_US-ljspeech-medium")]:
        directory = tmp_path / language
        directory.mkdir()
        model = directory / "voice.onnx"
        config = directory / "voice.onnx.json"
        model.write_bytes(voice_id.encode())
        config.write_text('{"audio":{"sample_rate":22050}}', encoding="utf-8")
        manifest["voices"][language] = {
            "voice_id": voice_id,
            "files": {
                "voice.onnx": hashlib.sha256(model.read_bytes()).hexdigest(),
                "voice.onnx.json": hashlib.sha256(config.read_bytes()).hexdigest(),
            },
        }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("PIPER_VOICE_DIR", str(tmp_path))
    return tmp_path, manifest


@pytest.fixture
def piper_runtime(monkeypatch):
    loads = []

    class Voice:
        def __init__(self, voice_id):
            self.voice_id = voice_id

        def synthesize_wav(self, text, wav_file):
            assert text == "Selected text."
            wav_file.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
            samples = {
                "da_DK-talesyntese-medium": b"\x01\x00",
                "en_US-ljspeech-medium": b"\x02\x00",
            }
            wav_file.writeframes(samples[self.voice_id])

    def load(path, *, config_path, use_cuda):
        loads.append((path, config_path, use_cuda))
        return Voice(Path(path).read_text())

    monkeypatch.setitem(sys.modules, "piper", SimpleNamespace(PiperVoice=SimpleNamespace(load=load)))
    return loads


def test_tts_loads_fixed_voices_from_language_artifact_paths_and_synthesizes_both(artifacts, piper_runtime):
    base, _manifest = artifacts
    adapter = create_tts_adapter()
    assert piper_runtime == [
        (str(base / "da" / "voice.onnx"), str(base / "da" / "voice.onnx.json"), False),
        (str(base / "en" / "voice.onnx"), str(base / "en" / "voice.onnx.json"), False),
    ]
    for language, samples in [("da", b"\x01\x00"), ("en", b"\x02\x00")]:
        with wave.open(io.BytesIO(adapter.synthesize_wav("Selected text.", language)), "rb") as wav_file:
            assert wav_file.getframerate() == 22050
            assert wav_file.readframes(1) == samples


@pytest.mark.parametrize("language", ["da", "en"])
@pytest.mark.parametrize("damage", [
    "missing_entry", "wrong_voice", "missing_model", "changed_model", "empty_model",
    "missing_config", "changed_config", "invalid_config", "invalid_sample_rate",
])
def test_tts_artifact_failure_keeps_readiness_false(artifacts, piper_runtime, language, damage):
    base, manifest = artifacts
    entry = manifest["voices"][language]
    if damage == "missing_entry":
        del manifest["voices"][language]
    elif damage == "wrong_voice":
        entry["voice_id"] = "arbitrary-voice"
    elif damage == "empty_model":
        model = base / language / "voice.onnx"
        model.write_bytes(b"")
        entry["files"]["voice.onnx"] = hashlib.sha256(b"").hexdigest()
    elif damage.startswith("missing_"):
        filename = "voice.onnx" if damage == "missing_model" else "voice.onnx.json"
        (base / language / filename).unlink()
    elif damage.startswith("changed_"):
        filename = "voice.onnx" if damage == "changed_model" else "voice.onnx.json"
        (base / language / filename).write_bytes(b"corrupt")
    else:
        config = base / language / "voice.onnx.json"
        content = "[]" if damage == "invalid_config" else '{"audio":{"sample_rate":"22050"}}'
        config.write_text(content, encoding="utf-8")
        entry["files"]["voice.onnx.json"] = hashlib.sha256(config.read_bytes()).hexdigest()
    (base / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(MissingArtifactError):
        create_tts_adapter()
    assert piper_runtime == []  # Validate the whole bundle before loading either voice.
    app = create_tts_app()
    with TestClient(app) as client:
        client.portal.call(_wait_for_initialization, app)
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "missing_artifact"}
    assert str(base) not in response.text


@pytest.mark.parametrize("damage", ["repository", "revision", "invalid_json", "invalid_shape"])
def test_tts_rejects_invalid_manifest(artifacts, piper_runtime, damage):
    base, manifest = artifacts
    if damage in {"repository", "revision"}:
        manifest[damage] = "unreviewed"
    content = json.dumps([] if damage == "invalid_shape" else manifest)
    if damage == "invalid_json":
        content = "{"
    (base / "manifest.json").write_text(content, encoding="utf-8")
    with pytest.raises(MissingArtifactError):
        create_tts_adapter()
    assert piper_runtime == []


@pytest.mark.parametrize("language", ["da", "en"])
def test_faster_whisper_adapter_passes_language_to_runtime(language):
    received = []

    def transcribe(audio, **kwargs):
        received.append((audio, kwargs))
        segments = [SimpleNamespace(start=0, end=1, text=" transcript ")]
        return segments, SimpleNamespace(language=language, duration=1)

    result = FasterWhisperAdapter(SimpleNamespace(transcribe=transcribe)).transcribe(b"audio", language)
    assert received == [(b"audio", {
        "language": language, "task": "transcribe", "beam_size": 1,
        "vad_filter": True, "log_progress": False,
    })]
    assert result == {
        "text": "transcript", "language": language, "duration": 1.0,
        "segments": [{"start": 0.0, "end": 1.0, "text": " transcript "}],
    }
