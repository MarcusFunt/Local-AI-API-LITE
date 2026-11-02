from __future__ import annotations

import asyncio

import chat_voice_test_imports  # noqa: F401
import httpx
import pytest
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

    def transcribe(self, audio, content_type, language):
        self.uploads.append((audio, content_type, language))
        return {"text": "redigerbar"}

    def synthesize(self, text, language):
        self.utterances.append((text, language))
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
        json={"language": "da", "preset": "fast", "messages": [{"role": "user", "content": "hi"}]},
    ).status_code == 401
    assert _request(app, "GET", "/docs", headers={"X-Example-Token": "controller-secret"}).status_code == 404
    assert _request(app, "GET", "/api/arbitrary", headers={"X-Example-Token": "controller-secret"}).status_code == 404


@pytest.mark.parametrize("language", ["da", "en"])
def test_api_routes_chat_transcription_and_synthesis_through_fixed_services(language):
    chat = FakeChat()
    speech = FakeSpeech()
    app = create_app(token="controller-secret", chat_service=chat, speech_service=speech)
    headers = {"X-Example-Token": "controller-secret"}

    response = _request(
        app,
        "POST",
        "/api/chat",
        json={"language": language, "preset": "quality", "messages": [{"role": "user", "content": "hi"}]},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["text"] == "chat answer"
    assert chat.requests[0].preset == "quality"
    assert chat.requests[0].language == language

    transcribed = _request(
        app,
        "POST",
        "/api/transcribe",
        files={"file": ("recording.webm", b"audio bytes", "audio/webm"), "language": (None, language)},
        headers=headers,
    )
    assert transcribed.status_code == 200
    assert transcribed.json() == {"text": "redigerbar"}
    assert speech.uploads == [(b"audio bytes", "audio/webm", language)]

    synthesized = _request(
        app,
        "POST",
        "/api/synthesize",
        json={"text": "Godmorgen", "language": language},
        headers=headers,
    )
    assert synthesized.status_code == 200
    assert synthesized.headers["content-type"] == "audio/wav"
    assert speech.utterances == [("Godmorgen", language)]


def test_api_rejects_extra_chat_options_invalid_multipart_and_oversized_body():
    app = create_app(token="controller-secret", chat_service=FakeChat(), speech_service=FakeSpeech())
    headers = {"X-Example-Token": "controller-secret"}
    invalid_chat = _request(
        app,
        "POST",
        "/api/chat",
        json={"language": "da", "preset": "fast", "messages": [{"role": "user", "content": "hi"}], "model": "other"},
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
        json={"language": "da", "preset": "fast", "messages": [{"role": "user", "content": "secret"}]},
        headers={"X-Example-Token": "attacker-secret"},
    )
    assert response.status_code == 503
    assert "attacker-secret" not in response.text


@pytest.mark.parametrize("path", ["/api/chat", "/api/transcribe", "/api/synthesize"])
@pytest.mark.parametrize("language", [None, "fr", "da-DK", "", 7, ["da"]])
def test_api_rejects_missing_or_unsupported_language(path, language):
    chat = FakeChat()
    speech = FakeSpeech()
    app = create_app(token="controller-secret", chat_service=chat, speech_service=speech)
    kwargs = {}
    if path == "/api/transcribe":
        files = [("file", ("private-name.webm", b"private-audio", "audio/webm"))]
        if language is not None:
            files.append(("language", (None, str(language))))
        kwargs["files"] = files
    else:
        body = (
            {"preset": "fast", "messages": [{"role": "user", "content": "private-prompt"}]}
            if path == "/api/chat" else {"text": "private-utterance"}
        )
        if language is not None:
            body["language"] = language
        kwargs["json"] = body
    response = _request(
        app, "POST", path, headers={"X-Example-Token": "controller-secret"}, **kwargs
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert chat.requests == []
    assert speech.uploads == []
    assert speech.utterances == []


@pytest.mark.parametrize(
    "extra_fields",
    [
        [("language", (None, "en"))],
        [("file", ("duplicate.webm", b"private-duplicate", "audio/webm"))],
        [("voice", (None, "private-voice"))],
    ],
)
def test_transcription_rejects_duplicate_and_extra_fields(extra_fields):
    speech = FakeSpeech()
    app = create_app(token="controller-secret", speech_service=speech)
    response = _request(
        app,
        "POST",
        "/api/transcribe",
        files=[
            ("file", ("recording.webm", b"private-audio", "audio/webm")),
            ("language", (None, "da")),
            *extra_fields,
        ],
        headers={"X-Example-Token": "controller-secret"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert speech.uploads == []
