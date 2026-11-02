from __future__ import annotations

from speech_worker.app import create_stt_app, create_tts_app
from speech_worker.contracts import (
    MAX_AUDIO_BYTES,
    AudioTooLongError,
    MissingArtifactError,
)
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
    def synthesize_wav(self, text):
        assert text == "God dag."
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
        assert model["revision"] == "320d5f7f7751a17ef6512d5c23863056c6a11c0f"
        speech = client.post(
            "/v1/audio/speech",
            json={"model": "ddu-tts", "input": "God dag.", "voice": "da_DK-talesyntese-medium"},
        )
        metrics = client.get("/metrics")
        wrong_model = client.post(
            "/v1/audio/speech",
            json={"model": "unknown", "input": "God dag.", "voice": "da_DK-talesyntese-medium"},
        )
        wrong_voice = client.post(
            "/v1/audio/speech",
            json={"model": "ddu-tts", "input": "God dag.", "voice": "another-voice"},
        )
        too_long = client.post(
            "/v1/audio/speech",
            json={"model": "ddu-tts", "input": "x" * 4_001},
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
