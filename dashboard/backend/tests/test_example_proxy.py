from __future__ import annotations

import asyncio
import json

import httpx
from conftest import AsgiTestClient, load_dashboard_module

from app.example_proxy import ExampleProxy
from app.example_runner import ExampleActivityConflict
from app.examples import ExampleDependencyStatus, ExamplePublicStatus


class ReadyRunner:
    def __init__(self, token="per-run-controller-token"):
        self.token = token
        self.activity_calls = 0
        self.status = ExamplePublicStatus(
            state="ready",
            chat_dependency=ExampleDependencyStatus(state="ready"),
            voice_dependency=ExampleDependencyStatus(state="ready"),
        )

    def get_upstream_token(self):
        if self.token is None:
            raise ExampleActivityConflict()
        return self.token

    def touch_activity(self):
        self.activity_calls += 1
        if self.token is None:
            raise ExampleActivityConflict()
        return self.status

    def list_status(self):
        return [self.status]


def _client(tmp_path, runner, proxy):
    api = load_dashboard_module("api")
    app = api.create_app(
        metadata_path=tmp_path / "metadata.sqlite3",
        example_runner=runner,
        example_proxy=proxy,
    )
    return AsgiTestClient(app)


def _proxy(runner, handler, **options):
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        return httpx.Client(transport=transport, **kwargs)

    return ExampleProxy(runner=runner, client_factory=client_factory, **options)


def _raw_request(client, method, path, *, content=None, headers=None, files=None):
    async def send():
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url=client.base_url,
            cookies=client.cookies,
        ) as request_client:
            response = await request_client.request(
                method,
                path,
                content=content,
                files=files,
                headers={"Origin": client.base_url, **(headers or {})},
            )
            client.cookies = request_client.cookies
            return response

    return asyncio.run(send())


def test_chat_proxy_forwards_fixed_payload_and_discards_browser_headers(tmp_path):
    runner = ReadyRunner()
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "text": "A useful answer",
                "elapsed_ms": 321,
                "usage": {"prompt_tokens": 7, "completion_tokens": 9},
            },
        )

    client = _client(tmp_path, runner, _proxy(runner, handler))
    client.setup_admin()

    response = client.post(
        "/api/control/v1/examples/chat-voice/chat",
        json_body={"preset": "balanced", "messages": [{"role": "user", "content": "hello"}]},
        headers={
            "X-CSRF-Token": client.csrf_token,
            "Authorization": "Bearer browser-secret",
            "X-Forwarded-Host": "attacker.example",
            "X-Example-Token": "attacker-token",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["text"] == "A useful answer"
    assert captured[0].url == "http://127.0.0.1:8766/api/chat"
    assert json.loads(captured[0].content) == {
        "preset": "balanced",
        "messages": [{"role": "user", "content": "hello"}],
    }
    assert captured[0].headers["x-example-token"] == runner.token
    assert captured[0].headers.get("authorization") is None
    assert captured[0].headers.get("cookie") is None
    assert captured[0].headers.get("x-csrf-token") is None
    assert captured[0].headers.get("x-forwarded-host") is None
    assert runner.activity_calls == 1


def test_transcription_forwards_only_a_bounded_audio_part(tmp_path):
    runner = ReadyRunner()
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json={"text": "editable transcript"})

    client = _client(tmp_path, runner, _proxy(runner, handler))
    client.setup_admin()

    response = _raw_request(
        client,
        "POST",
        "/api/control/v1/examples/chat-voice/transcribe",
        files={"file": ("voice.webm", b"bounded-audio", "audio/webm")},
        headers={"X-CSRF-Token": client.csrf_token},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"text": "editable transcript"}
    assert captured[0].url == "http://127.0.0.1:8766/api/transcribe"
    forwarded_body = captured[0].read().decode("latin1")
    assert 'filename="recording"' in forwarded_body
    assert "Content-Type: audio/webm" in forwarded_body


def test_synthesis_returns_bounded_wav_without_forwarding_browser_secrets(tmp_path):
    runner = ReadyRunner()
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, content=b"RIFF\x00\x00\x00\x00WAVE", headers={"Content-Type": "audio/wav"})

    client = _client(tmp_path, runner, _proxy(runner, handler))
    client.setup_admin()

    response = client.post(
        "/api/control/v1/examples/chat-voice/synthesize",
        json_body={"text": "A spoken reply"},
        headers={
            "X-CSRF-Token": client.csrf_token,
            "Cookie": (
                f"dashboard_session={client.cookies.get('dashboard_session')}; "
                "browser-session=secret"
            ),
        },
    )

    assert response.status_code == 200
    assert response.content.startswith(b"RIFF")
    assert response.headers["content-type"] == "audio/wav"
    assert json.loads(captured[0].content) == {"text": "A spoken reply"}
    assert captured[0].headers.get("cookie") is None
    assert captured[0].headers.get("x-csrf-token") is None


def test_chat_proxy_requires_session_csrf_and_local_host_origin(tmp_path):
    runner = ReadyRunner()
    proxy = _proxy(runner, lambda _request: httpx.Response(200, json={"text": "ok"}))
    client = _client(tmp_path, runner, proxy)
    body = {"preset": "fast", "messages": [{"role": "user", "content": "hi"}]}

    assert client.post("/api/control/v1/examples/chat-voice/chat", json_body=body).status_code == 401
    client.setup_admin()
    assert client.post("/api/control/v1/examples/chat-voice/chat", json_body=body).status_code == 403
    assert client.post(
        "/api/control/v1/examples/chat-voice/chat",
        json_body=body,
        headers={"X-CSRF-Token": client.csrf_token, "Origin": "http://evil.example"},
    ).status_code == 403
    assert client.post(
        "/api/control/v1/examples/chat-voice/chat",
        json_body=body,
        headers={"X-CSRF-Token": client.csrf_token, "Host": "evil.example"},
    ).status_code == 403
    assert client.request("GET", "/api/control/v1/examples/chat-voice/chat").status_code == 405
    assert client.request("PUT", "/api/control/v1/examples/chat-voice/transcribe").status_code == 405
    assert client.post("/api/control/v1/examples/chat-voice/arbitrary", headers={"X-CSRF-Token": client.csrf_token}).status_code == 404


def test_proxy_rejects_unready_example_and_unknown_chat_options(tmp_path):
    runner = ReadyRunner(token=None)
    proxy = _proxy(runner, lambda _request: httpx.Response(200, json={"text": "not reached"}))
    client = _client(tmp_path, runner, proxy)
    client.setup_admin()
    headers = {"X-CSRF-Token": client.csrf_token}

    unavailable = client.post(
        "/api/control/v1/examples/chat-voice/chat",
        json_body={"preset": "fast", "messages": [{"role": "user", "content": "hi"}]},
        headers=headers,
    )
    unsupported = client.post(
        "/api/control/v1/examples/chat-voice/chat",
        json_body={
            "preset": "fast",
            "messages": [{"role": "user", "content": "hi"}],
            "model": "other-model",
        },
        headers=headers,
    )

    assert unavailable.status_code == 409
    assert unsupported.status_code == 422


def test_proxy_sanitizes_upstream_connection_timeout_and_http_errors(tmp_path):
    for failure in (
        httpx.ConnectError("C:/Users/private/upstream.log"),
        httpx.ReadTimeout("private request details"),
    ):
        runner = ReadyRunner()

        def handler(_request, cause=failure):
            raise cause

        client = _client(tmp_path / str(type(failure).__name__), runner, _proxy(runner, handler))
        client.setup_admin()
        response = client.post(
            "/api/control/v1/examples/chat-voice/chat",
            json_body={"preset": "fast", "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-CSRF-Token": client.csrf_token},
        )
        assert response.status_code in {502, 503, 504}
        assert "private" not in response.text


def test_proxy_rejects_malformed_and_oversized_multipart_before_forwarding(tmp_path):
    runner = ReadyRunner()
    calls = []
    proxy = _proxy(runner, lambda request: calls.append(request) or httpx.Response(200, json={"text": "ok"}))
    client = _client(tmp_path, runner, proxy)
    client.setup_admin()
    headers = {"X-CSRF-Token": client.csrf_token, "Content-Type": "multipart/form-data; boundary=missing"}

    malformed = _raw_request(
        client,
        "POST",
        "/api/control/v1/examples/chat-voice/transcribe",
        content=b"not a multipart body",
        headers=headers,
    )
    oversized = _raw_request(
        client,
        "POST",
        "/api/control/v1/examples/chat-voice/transcribe",
        content=b"x",
        headers={
            "X-CSRF-Token": client.csrf_token,
            "Content-Type": "multipart/form-data; boundary=missing",
            "Content-Length": "25070000",
        },
    )

    assert malformed.status_code in {400, 422}
    assert oversized.status_code == 413
    assert calls == []


def test_proxy_rejects_oversized_upstream_responses(tmp_path):
    runner = ReadyRunner()
    proxy = _proxy(runner, lambda _request: httpx.Response(200, content=b"x" * 600_000))
    client = _client(tmp_path, runner, proxy)
    client.setup_admin()

    response = client.post(
        "/api/control/v1/examples/chat-voice/chat",
        json_body={"preset": "balanced", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-CSRF-Token": client.csrf_token},
    )

    assert response.status_code == 502
    assert "x" * 100 not in response.text
