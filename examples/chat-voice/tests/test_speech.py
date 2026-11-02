from __future__ import annotations

import io
import json
import logging
import threading
import wave
from fractions import Fraction

import av
import chat_voice_test_imports  # noqa: F401
import httpx
import pytest
from chat_voice_app.errors import ServiceError
from chat_voice_app.speech import (
    MAX_AUDIO_BYTES,
    MAX_AUDIO_SECONDS,
    MAX_TTS_CHARACTERS,
    SpeechService,
)


def _wav(seconds=1, payload=b""):
    output = io.BytesIO()
    samples = seconds * 8000
    data = (payload + b"\x00\x00" * samples)[: samples * 2]
    with wave.open(output, "wb") as recorder:
        recorder.setnchannels(1)
        recorder.setsampwidth(2)
        recorder.setframerate(8000)
        recorder.writeframes(data)
    return output.getvalue()


def _opus_webm(seconds):
    output = io.BytesIO()
    with av.open(output, mode="w", format="webm") as container:
        stream = container.add_stream("libopus", rate=48000)
        stream.layout = "mono"
        for index in range(seconds * 50):
            frame = av.AudioFrame(format="s16", layout="mono", samples=960)
            frame.sample_rate = 48000
            frame.pts = index * frame.samples
            frame.time_base = Fraction(1, 48000)
            frame.planes[0].update(b"\x00\x00" * frame.samples)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return output.getvalue()


def _service(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, **kwargs)

    return SpeechService(client_factory=factory)


def test_transcription_uses_fixed_danish_model_and_returns_editable_text():
    captured = []
    service = _service(
        lambda request: captured.append(request) or httpx.Response(200, json={"text": "redigerbar tekst"})
    )

    assert service.transcribe(_wav(), "audio/wav") == {"text": "redigerbar tekst"}
    request = captured[0]
    assert request.url == "http://ddu-stt:8080/v1/audio/transcriptions"
    body = request.read().decode("latin1")
    assert "ddu-stt" in body
    assert "\r\n\r\nda\r\n" in body
    assert "\r\n\r\njson\r\n" in body
    assert "Content-Type: audio/wav" in body


def test_synthesis_uses_fixed_voice_and_wav_response():
    captured = []
    service = _service(
        lambda request: captured.append(request)
        or httpx.Response(200, content=b"RIFF\x04\x00\x00\x00WAVE", headers={"Content-Type": "audio/wav"})
    )

    audio, content_type = service.synthesize("Godmorgen")
    assert audio.startswith(b"RIFF")
    assert content_type == "audio/wav"
    assert captured[0].url == "http://ddu-tts:8080/v1/audio/speech"
    assert json.loads(captured[0].content) == {
        "model": "ddu-tts",
        "voice": "da_DK-talesyntese-medium",
        "input": "Godmorgen",
        "response_format": "wav",
    }


def test_profile_limits_and_invalid_audio_or_long_speech_are_enforced():
    calls = []
    service = _service(lambda request: calls.append(request) or httpx.Response(200, json={"text": "ok"}))
    assert MAX_AUDIO_BYTES == 25_000_000
    assert MAX_AUDIO_SECONDS == 120
    assert MAX_TTS_CHARACTERS == 4000
    with pytest.raises(ServiceError) as oversized:
        service.transcribe(b"x" * (MAX_AUDIO_BYTES + 1), "audio/webm")
    assert oversized.value.status_code == 413
    with pytest.raises(ServiceError) as invalid_media:
        service.transcribe(b"not audio", "application/octet-stream")
    assert invalid_media.value.status_code == 415
    with pytest.raises(ServiceError) as long_text:
        service.synthesize("x" * (MAX_TTS_CHARACTERS + 1))
    assert long_text.value.status_code == 422
    assert calls == []


def test_transcription_rejects_audio_over_the_duration_limit_before_stt():
    calls = []
    service = _service(lambda request: calls.append(request) or httpx.Response(200, json={"text": "ok"}))
    recording = _opus_webm(seconds=121)
    assert len(recording) < MAX_AUDIO_BYTES

    with pytest.raises(ServiceError) as too_long:
        service.transcribe(recording, "audio/webm")

    assert too_long.value.status_code == 413
    assert calls == []


def test_transcription_accepts_audio_at_the_duration_limit():
    calls = []
    service = _service(lambda request: calls.append(request) or httpx.Response(200, json={"text": "ok"}))

    assert service.transcribe(_wav(seconds=MAX_AUDIO_SECONDS), "audio/wav") == {"text": "ok"}
    assert len(calls) == 1


def test_speech_timeout_and_upstream_failure_are_sanitized_and_content_free(caplog):
    sensitive = "private-audio-transcript-5pq"
    service = _service(lambda _request: (_ for _ in ()).throw(httpx.ConnectTimeout(sensitive)))
    caplog.set_level(logging.INFO)
    with pytest.raises(ServiceError) as error:
        service.transcribe(_wav(payload=sensitive.encode()), "audio/wav")
    assert error.value.status_code == 504
    assert sensitive not in str(error.value)
    assert sensitive not in caplog.text


def test_speech_concurrency_saturation_is_bounded():
    entered = threading.Event()
    release = threading.Event()

    def handler(_request):
        entered.set()
        release.wait(timeout=2)
        return httpx.Response(200, json={"text": "done"})

    service = _service(handler)
    first_result = []
    worker = threading.Thread(target=lambda: first_result.append(service.transcribe(_wav(), "audio/wav")))
    worker.start()
    assert entered.wait(timeout=1)
    with pytest.raises(ServiceError) as busy:
        service.transcribe(_wav(), "audio/wav")
    assert busy.value.status_code == 429
    release.set()
    worker.join(timeout=2)
    assert first_result == [{"text": "done"}]
