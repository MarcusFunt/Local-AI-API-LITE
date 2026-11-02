from __future__ import annotations

import asyncio

import chat_voice_test_imports  # noqa: F401
import httpx
from chat_voice_app.main import create_app


class FakeChat:
    def __init__(self):
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return {"text": "chat answer", "elapsed_ms": 12, "usage": {"prompt_tokens": 3}}


class FakeSpeech:
    def __init__(self):
        self.uploads = []
        self.utterances = []

    def transcribe(self, audio, content_type):
        self.uploads.append((audio, content_type))
        return {"text": "redigerbar"}

    def synthesize(self, text):
        self.utterances.append(text)
        return b"RIFF\x04\x00\x00\x00WAVE", "audio/wav"


def _request(app, method, path, *, json=None, content=None, files=None, headers=None):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.request(
                method,
                path,
                json=json,
                content=content,
                files=files,
                headers=headers,
            )

    return asyncio.run(send())


def test_api_requires_private_token_and_exposes_only_health():
    app = create_app(token="controller-secret", chat_service=FakeChat(), speech_service=FakeSpeech())
    assert _request(app, "GET", "/health").status_code == 200
    assert _request(
        app,
        "POST",
        "/api/chat",
        json={"preset": "fast", "messages": [{"role": "user", "content": "hi"}]},
    ).status_code == 401
    assert _request(app, "GET", "/docs", headers={"X-Example-Token": "controller-secret"}).status_code == 404
    assert _request(app, "GET", "/api/arbitrary", headers={"X-Example-Token": "controller-secret"}).status_code == 404


def test_api_routes_chat_transcription_and_synthesis_through_fixed_services():
    chat = FakeChat()
    speech = FakeSpeech()
    app = create_app(token="controller-secret", chat_service=chat, speech_service=speech)
    headers = {"X-Example-Token": "controller-secret"}

    response = _request(
        app,
        "POST",
        "/api/chat",
        json={"preset": "quality", "messages": [{"role": "user", "content": "hi"}]},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["text"] == "chat answer"
    assert chat.requests[0].preset == "quality"

    transcribed = _request(
        app,
        "POST",
        "/api/transcribe",
        files={"file": ("recording.webm", b"audio bytes", "audio/webm")},
        headers=headers,
    )
    assert transcribed.status_code == 200
    assert transcribed.json() == {"text": "redigerbar"}
    assert speech.uploads == [(b"audio bytes", "audio/webm")]

    synthesized = _request(
        app,
        "POST",
        "/api/synthesize",
        json={"text": "Godmorgen"},
        headers=headers,
    )
    assert synthesized.status_code == 200
    assert synthesized.headers["content-type"] == "audio/wav"
    assert speech.utterances == ["Godmorgen"]


def test_api_rejects_extra_chat_options_invalid_multipart_and_oversized_body():
    app = create_app(token="controller-secret", chat_service=FakeChat(), speech_service=FakeSpeech())
    headers = {"X-Example-Token": "controller-secret"}
    invalid_chat = _request(
        app,
        "POST",
        "/api/chat",
        json={"preset": "fast", "messages": [{"role": "user", "content": "hi"}], "model": "other"},
        headers=headers,
    )
    assert invalid_chat.status_code == 422
    assert "hi" not in invalid_chat.text
    malformed = _request(
        app,
        "POST",
        "/api/transcribe",
        content=b"not multipart",
        headers={**headers, "Content-Type": "multipart/form-data; boundary=missing"},
    )
    assert malformed.status_code == 400
    oversized = _request(
        app,
        "POST",
        "/api/transcribe",
        content=b"x",
        headers={
            **headers,
            "Content-Type": "multipart/form-data; boundary=missing",
            "Content-Length": "25070000",
        },
    )
    assert oversized.status_code == 413


def test_api_reports_missing_configured_token_without_echoing_header():
    app = create_app(token="", chat_service=FakeChat(), speech_service=FakeSpeech())
    response = _request(
        app,
        "POST",
        "/api/chat",
        json={"preset": "fast", "messages": [{"role": "user", "content": "secret"}]},
        headers={"X-Example-Token": "attacker-secret"},
    )
    assert response.status_code == 503
    assert "attacker-secret" not in response.text
