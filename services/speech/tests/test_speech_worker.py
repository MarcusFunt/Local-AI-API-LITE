from __future__ import annotations

import asyncio
import io
import threading
import wave
from concurrent.futures import ThreadPoolExecutor

import pytest
from speech_worker.app import create_stt_app, create_tts_app
from speech_worker.contracts import (
    MAX_AUDIO_BYTES,
    AudioTooLongError,
    MissingArtifactError,
)
from speech_worker.models import PiperAdapter
from starlette.testclient import TestClient


class FakeTranscriber:
    def transcribe(self, audio, language):
        assert audio == b"decoded audio"
        assert language == "da"
        return {
            "text": "Den danske tekst",
            "language": "da",
            "duration": 1.25,
            "segments": [{"start": 0.0, "end": 1.25, "text": "Den danske tekst"}],
        }


class FakeSynthesizer:
    def synthesize_wav(self, text, language):
        assert text == "God dag."
        assert language == "da"
        return b"RIFF\x00\x00\x00\x00WAVE\x00"


def _client(app):
    return TestClient(app)


def test_stt_reports_pinned_identity_and_transcribes_bounded_danish_audio():
    app = create_stt_app(
        adapter_factory=FakeTranscriber,
        audio_decoder=lambda _file, _limit: (b"decoded audio", 1.25),
    )
    with _client(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").json() == {"status": "ready"}
        model = client.get("/v1/models").json()["data"][0]
        assert model["id"] == "ddu-stt"
        assert model["revision"] == "536b0662742c02347bc0e980a01041f333bce120"
        response = client.post(
            "/v1/audio/transcriptions",
            data={"model": "ddu-stt", "language": "da", "response_format": "verbose_json"},
            files={"file": ("sample.wav", b"sample audio", "audio/wav")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "text": "Den danske tekst",
        "language": "da",
        "duration": 1.25,
        "segments": [{"start": 0.0, "end": 1.25, "text": "Den danske tekst"}],
    }


def test_stt_rejects_unknown_model_and_overlong_audio_with_sanitized_errors():
    app = create_stt_app(
        adapter_factory=FakeTranscriber,
        audio_decoder=lambda _file, _limit: (_ for _ in ()).throw(AudioTooLongError()),
    )
    with _client(app) as client:
        wrong_model = client.post(
            "/v1/audio/transcriptions",
            data={"model": "unreviewed-model", "language": "da"},
            files={"file": ("sample.wav", b"sample audio", "audio/wav")},
        )
        too_long = client.post(
            "/v1/audio/transcriptions",
            data={"model": "ddu-stt", "language": "da"},
            files={"file": ("sample.wav", b"sample audio", "audio/wav")},
        )

    assert wrong_model.status_code == 422
    assert "unsupported_model" in wrong_model.text
    assert too_long.status_code == 413
    assert "audio_duration_limit" in too_long.text


def test_stt_rejects_oversized_upload_before_running_the_model():
    called = False

    class CountingTranscriber:
        def transcribe(self, _audio, _language):
            nonlocal called
            called = True
            return {"text": "unexpected"}

    app = create_stt_app(
        adapter_factory=CountingTranscriber,
        audio_decoder=lambda *_args: (_ for _ in ()).throw(AssertionError("oversized file was decoded")),
    )
    with _client(app) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            data={"model": "ddu-stt", "language": "da"},
            files={"file": ("sample.wav", b"x" * (MAX_AUDIO_BYTES + 1), "audio/wav")},
        )

    assert response.status_code == 413
    assert "audio_size_limit" in response.text
    assert called is False


def test_stt_does_not_expose_model_exceptions_or_audio_content():
    class BrokenTranscriber:
        def transcribe(self, _audio, _language):
            raise RuntimeError("private transcript must not escape")

    app = create_stt_app(
        adapter_factory=BrokenTranscriber,
        audio_decoder=lambda _file, _limit: (b"private audio bytes", 1.0),
    )
    with _client(app) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            data={"model": "ddu-stt", "language": "da"},
            files={"file": ("private-filename.wav", b"private audio bytes", "audio/wav")},
        )
        metrics = client.get("/metrics")

    assert response.status_code == 500
    assert "private transcript" not in response.text
    assert "private-filename" not in response.text
    assert metrics.status_code == 200
    assert "private" not in metrics.text


def test_tts_returns_wav_and_rejects_unrecognized_model_or_voice():
    app = create_tts_app(adapter_factory=FakeSynthesizer)
    with _client(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").json() == {"status": "ready"}
        model = client.get("/v1/models").json()["data"][0]
        assert model["id"] == "ddu-tts"
        assert model["revision"] == "375a0fe641dea077c2a47b4e9a056d6da521eed3"
        speech = client.post(
            "/v1/audio/speech",
            json={"model": "ddu-tts", "input": "God dag.", "language": "da"},
        )
        metrics = client.get("/metrics")
        wrong_model = client.post(
            "/v1/audio/speech",
            json={"model": "unknown", "input": "God dag.", "language": "da"},
        )
        wrong_voice = client.post(
            "/v1/audio/speech",
            json={"model": "ddu-tts", "input": "God dag.", "language": "da", "voice": "another-voice"},
        )
        too_long = client.post(
            "/v1/audio/speech",
            json={"model": "ddu-tts", "input": "x" * 4_001, "language": "da"},
        )

    assert speech.status_code == 200
    assert speech.headers["content-type"].startswith("audio/wav")
    assert speech.content.startswith(b"RIFF")
    assert isinstance(metrics.json()["last_successful_inference_at"], str)
    assert wrong_model.status_code == 422
    assert wrong_voice.status_code == 422
    assert too_long.status_code == 422


def test_readiness_stays_false_when_the_pinned_model_is_missing():
    def missing_model():
        raise MissingArtifactError()

    app = create_stt_app(adapter_factory=missing_model)
    with _client(app) as client:
        assert client.get("/health").status_code == 200
        readiness = client.get("/ready")

    assert readiness.status_code == 503
    assert readiness.json() == {"status": "missing_artifact"}


@pytest.mark.parametrize("language", ["da", "en"])
def test_stt_forwards_each_supported_language_to_adapter(language):
    received = []

    class Transcriber:
        def transcribe(self, audio, language):
            received.append((audio, language))
            # Missing output language must default to the requested language too.
            return {"text": "transcript"}

    app = create_stt_app(
        adapter_factory=Transcriber,
        audio_decoder=lambda _file, _limit: (b"decoded audio", 1.25),
    )
    with _client(app) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            data={"model": "ddu-stt", "language": language, "response_format": "verbose_json"},
            files={"file": ("sample.wav", b"sample audio", "audio/wav")},
        )
    assert response.status_code == 200
    assert received == [(b"decoded audio", language)]
    assert response.json()["language"] == language


@pytest.mark.parametrize("language", ["fr", "da-DK"])
def test_stt_rejects_unsupported_language(language):
    decoded = []

    def decode(_file, _limit):
        decoded.append(True)
        return b"decoded audio", 1.25

    app = create_stt_app(
        adapter_factory=FakeTranscriber,
        audio_decoder=decode,
    )
    with _client(app) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            data={"model": "ddu-stt", "language": language},
            files={"file": ("sample.wav", b"sample audio", "audio/wav")},
        )
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "invalid_request", "message": "Request is invalid."}}
    assert decoded == []


@pytest.mark.parametrize("fields", [{}, {"language": ""}], ids=["missing", "empty"])
def test_stt_rejects_missing_or_empty_language_before_decode_or_inference(fields):
    decoded, transcribed = [], []

    def decode(_file, _limit):
        decoded.append(True)
        return b"decoded audio", 1.25

    class Transcriber:
        def transcribe(self, audio, language):
            transcribed.append((audio, language))
            return {"text": "unexpected", "language": language}

    app = create_stt_app(adapter_factory=Transcriber, audio_decoder=decode)
    with _client(app) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            data={"model": "ddu-stt", **fields},
            files={"file": ("sample.wav", b"sample audio", "audio/wav")},
        )
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "invalid_request", "message": "Request is invalid."}}
    assert decoded == []
    assert transcribed == []


@pytest.mark.parametrize(
    ("language", "voice_id", "samples"),
    [("da", "da_DK-talesyntese-medium", b"\x01\x00"), ("en", "en_US-ljspeech-medium", b"\x02\x00")],
)
def test_tts_selects_the_fixed_voice_for_each_language(language, voice_id, samples):
    used_voices = []

    class Voice:
        def __init__(self, voice_id, samples):
            self.voice_id, self.samples = voice_id, samples

        def synthesize_wav(self, text, wav_file):
            used_voices.append((self.voice_id, text))
            wav_file.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
            wav_file.writeframes(self.samples)

    adapter = PiperAdapter({
        "da": Voice("da_DK-talesyntese-medium", b"\x01\x00"),
        "en": Voice("en_US-ljspeech-medium", b"\x02\x00"),
    })
    app = create_tts_app(adapter_factory=lambda: adapter)
    with _client(app) as client:
        response = client.post(
            "/v1/audio/speech", json={"model": "ddu-tts", "input": "Selected text.", "language": language},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    with wave.open(io.BytesIO(response.content), "rb") as wav_file:
        assert wav_file.readframes(1) == samples
    assert used_voices == [(voice_id, "Selected text.")]


@pytest.mark.parametrize("fields", [
    {"language": "fr"}, {"language": "da-DK"}, {},
    {"language": "da", "voice": "da_DK-talesyntese-medium"},
    {"language": "en", "voice": "arbitrary-voice"},
])
def test_tts_rejects_unsupported_language_and_arbitrary_voice(fields):
    app = create_tts_app(adapter_factory=FakeSynthesizer)
    with _client(app) as client:
        response = client.post("/v1/audio/speech", json={"model": "ddu-tts", "input": "God dag.", **fields})
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "invalid_request", "message": "Request is invalid."}}


def test_models_metadata_lists_both_languages_and_voice_ids():
    for create_app, adapter in [(create_stt_app, FakeTranscriber), (create_tts_app, FakeSynthesizer)]:
        with _client(create_app(adapter_factory=adapter)) as client:
            model = client.get("/v1/models").json()["data"][0]
        assert model["languages"] == ["da", "en"]
        assert "path" not in str(model).lower()
        if model["id"] == "ddu-tts":
            assert model["voice_ids"] == {"da": "da_DK-talesyntese-medium", "en": "en_US-ljspeech-medium"}


def test_tts_serializes_both_languages_and_rejects_a_third_outstanding_request():
    entered, release = threading.Event(), threading.Event()
    received = []

    class BlockingSynthesizer:
        def synthesize_wav(self, text, language):
            received.append((text, language))
            entered.set()
            if not release.wait(timeout=2):
                raise RuntimeError("test inference was not released")
            return b"RIFF\x00\x00\x00\x00WAVE\x00"

    app = create_tts_app(adapter_factory=BlockingSynthesizer)

    async def wait_for_second_request():
        async def wait():
            while app.state.inference_queue.snapshot()["outstanding_requests"] != 2:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait(), timeout=1)

    def synthesize(client, language):
        return client.post("/v1/audio/speech", json={"model": "ddu-tts", "input": "Selected text.", "language": language})

    with _client(app) as client, ThreadPoolExecutor(max_workers=2) as requests:
        try:
            first = requests.submit(synthesize, client, "da")
            assert entered.wait(timeout=1)
            second = requests.submit(synthesize, client, "en")
            client.portal.call(wait_for_second_request)
            assert received == [("Selected text.", "da")]
            rejected = synthesize(client, "en")
            assert rejected.status_code == 429
            assert rejected.json()["error"]["code"] == "capacity"
            assert client.get("/ready").status_code == 503
        finally:
            release.set()
        assert first.result(timeout=1).status_code == 200
        assert second.result(timeout=1).status_code == 200
        assert received == [("Selected text.", "da"), ("Selected text.", "en")]
        assert client.get("/ready").json() == {"status": "ready"}


def test_tts_timeout_keeps_worker_degraded_until_inference_finishes():
    release = threading.Event()

    class SlowSynthesizer:
        def synthesize_wav(self, text, language):
            assert (text, language) == ("Selected text.", "en")
            release.wait(timeout=2)
            return b"RIFF\x00\x00\x00\x00WAVE\x00"

    app = create_tts_app(adapter_factory=SlowSynthesizer, inference_timeout_seconds=0.02)
    with _client(app) as client:
        try:
            response = client.post("/v1/audio/speech", json={"model": "ddu-tts", "input": "Selected text.", "language": "en"})
            assert response.status_code == 504
            assert response.json() == {"error": {"code": "timeout", "message": "Speech inference timed out."}}
            assert client.get("/ready").json() == {"status": "degraded", "reason_category": "inference_capacity"}
            assert client.get("/metrics").json()["stuck_inference"] is True
        finally:
            release.set()


def test_tts_failure_sanitizes_private_text_and_voice_paths():
    class BrokenSynthesizer:
        def synthesize_wav(self, text, language):
            raise RuntimeError(f"{text}: /models/speech/piper/{language}/voice.onnx")

    with _client(create_tts_app(adapter_factory=BrokenSynthesizer)) as client:
        response = client.post("/v1/audio/speech", json={"model": "ddu-tts", "input": "private text", "language": "en"})
        metrics = client.get("/metrics")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "inference_failed", "message": "Speech synthesis failed."}}
    assert "private text" not in metrics.text
    assert "/models" not in metrics.text
