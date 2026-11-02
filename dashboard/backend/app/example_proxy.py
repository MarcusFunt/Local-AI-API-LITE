from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.example_runner import ExampleActivityConflict
from app.examples import get_example

CHAT_BODY_LIMIT = 512_000
TRANSCRIBE_OVERHEAD_LIMIT = 64_000
SYNTHESIS_BODY_LIMIT = 64_000
CHAT_RESPONSE_LIMIT = 256_000
TRANSCRIPT_RESPONSE_LIMIT = 32_000
SYNTHESIS_RESPONSE_LIMIT = 25_000_000
ALLOWED_AUDIO_TYPES = frozenset(
    {"audio/webm", "audio/wav", "audio/x-wav", "audio/ogg", "audio/mpeg", "audio/mp4"}
)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=16_000)


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    language: Literal["da", "en"]
    preset: Literal["fast", "balanced", "quality"]
    messages: list[ChatMessage] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_conversation(self) -> ChatTurnRequest:
        if self.messages[-1].role != "user":
            raise ValueError("The last message must be from the user")
        if sum(len(message.content) for message in self.messages) > 64_000:
            raise ValueError("Conversation is too long")
        return self


class SynthesizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=1, max_length=4000)
    language: Literal["da", "en"]


class ExampleProxyError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class ExampleRequestSizeLimitMiddleware:
    """Buffer only the three fixed proxy bodies and reject them above a hard cap."""

    def __init__(self, app):
        self.app = app
        self._capacity = threading.BoundedSemaphore(4)
        self._limits = {
            "/api/control/v1/examples/chat-voice/chat": CHAT_BODY_LIMIT,
            "/api/control/v1/examples/chat-voice/transcribe": (
                get_example("chat-voice").max_audio_bytes + TRANSCRIBE_OVERHEAD_LIMIT
            ),
            "/api/control/v1/examples/chat-voice/synthesize": SYNTHESIS_BODY_LIMIT,
        }

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        limit = self._limits.get(scope.get("path", ""))
        if limit is None:
            await self.app(scope, receive, send)
            return

        if not self._capacity.acquire(blocking=False):
            await self._reject(send, 429, "Too many active example requests")
            return
        try:
            await self._bounded_request(scope, receive, send, limit)
        finally:
            self._capacity.release()

    async def _bounded_request(self, scope, receive, send, limit: int) -> None:

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                await self._reject(send, 413, "Request body is too large")
                return
            if declared_length < 0 or declared_length > limit:
                await self._reject(send, 413, "Request body is too large")
                return

        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > limit:
                await self._reject(send, 413, "Request body is too large")
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        delivered = False

        async def replay_body():
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_body, send)

    @staticmethod
    async def _reject(send, status_code: int, detail: str) -> None:
        body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


class ExampleProxy:
    """Translate three typed dashboard operations to the fixed local app API."""

    def __init__(
        self,
        runner,
        *,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self.runner = runner
        self.client_factory = client_factory
        self.timeout = timeout or httpx.Timeout(connect=3, read=125, write=15, pool=3)
        example = get_example("chat-voice")
        self._base_url = f"http://{example.host_ip}:{example.host_port}"
        self._max_audio_bytes = example.max_audio_bytes
        self._max_tts_characters = example.max_tts_characters

    @property
    def max_audio_bytes(self) -> int:
        return self._max_audio_bytes

    def chat(self, request: ChatTurnRequest) -> dict[str, object]:
        payload = {
            "language": request.language,
            "preset": request.preset,
            "messages": [message.model_dump(mode="json") for message in request.messages],
        }
        response = self._post_json("/api/chat", payload, CHAT_RESPONSE_LIMIT)
        if not isinstance(response, dict) or not isinstance(response.get("text"), str):
            raise ExampleProxyError(502, "The chat service returned an invalid response")
        text = response["text"]
        if len(text) > 64_000:
            raise ExampleProxyError(502, "The chat service returned an oversized response")
        result: dict[str, object] = {"text": text}
        elapsed = response.get("elapsed_ms")
        if isinstance(elapsed, int) and not isinstance(elapsed, bool) and 0 <= elapsed <= 600_000:
            result["elapsed_ms"] = elapsed
        usage = response.get("usage")
        if isinstance(usage, dict):
            safe_usage = {
                key: value
                for key, value in usage.items()
                if key in {"prompt_tokens", "completion_tokens"}
                and isinstance(value, int)
                and not isinstance(value, bool)
                and 0 <= value <= 100_000
            }
            if safe_usage:
                result["usage"] = safe_usage
        return result

    def transcribe(self, audio: bytes, content_type: str, language: Literal["da", "en"]) -> dict[str, str]:
        if not audio or len(audio) > self._max_audio_bytes:
            raise ExampleProxyError(413 if len(audio) > self._max_audio_bytes else 422, "Audio upload is invalid")
        if content_type not in ALLOWED_AUDIO_TYPES:
            raise ExampleProxyError(415, "Audio format is not supported")
        response = self._post_multipart(audio, content_type, language)
        if not isinstance(response, dict) or not isinstance(response.get("text"), str):
            raise ExampleProxyError(502, "The speech service returned an invalid response")
        text = response["text"]
        if len(text) > self._max_tts_characters:
            raise ExampleProxyError(502, "The speech service returned an oversized transcript")
        return {"text": text}

    def synthesize(self, request: SynthesizeRequest) -> tuple[bytes, str]:
        if len(request.text) > self._max_tts_characters:
            raise ExampleProxyError(422, "Speech text is too long")
        body = self._request(
            "POST",
            "/api/synthesize",
            json={"text": request.text, "language": request.language},
            response_limit=SYNTHESIS_RESPONSE_LIMIT,
            accept="audio/wav",
        )
        content_type = body.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"audio/wav", "audio/x-wav"}:
            raise ExampleProxyError(502, "The speech service returned an invalid audio format")
        return body.content, "audio/wav"

    def _post_json(self, path: str, payload: dict[str, object], response_limit: int) -> dict:
        response = self._request("POST", path, json=payload, response_limit=response_limit)
        try:
            result = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ExampleProxyError(502, "The example service returned an invalid response") from None
        if not isinstance(result, dict):
            raise ExampleProxyError(502, "The example service returned an invalid response")
        return result

    def _post_multipart(self, audio: bytes, content_type: str, language: Literal["da", "en"]) -> dict:
        response = self._request(
            "POST",
            "/api/transcribe",
            files={
                "file": ("recording", audio, content_type),
                "language": (None, language),
            },
            response_limit=TRANSCRIPT_RESPONSE_LIMIT,
        )
        try:
            result = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ExampleProxyError(502, "The example service returned an invalid response") from None
        if not isinstance(result, dict):
            raise ExampleProxyError(502, "The example service returned an invalid response")
        return result

    def _request(self, method: str, path: str, *, response_limit: int, **kwargs) -> httpx.Response:
        try:
            self.runner.touch_activity()
            token = self.runner.get_upstream_token()
        except ExampleActivityConflict:
            raise ExampleProxyError(409, "The Chat + Voice example is not ready") from None
        headers = {
            "X-Example-Token": token,
            "Accept": kwargs.pop("accept", "application/json"),
            "Accept-Encoding": "identity",
        }
        try:
            with (
                self.client_factory(timeout=self.timeout, trust_env=False) as client,
                client.stream(
                    method,
                    self._base_url + path,
                    headers=headers,
                    **kwargs,
                ) as response,
            ):
                    if response.status_code < 200 or response.status_code >= 300:
                        raise ExampleProxyError(503, "The example service is unavailable")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ExampleProxyError(502, "The example service returned an invalid response")
                    declared_size = response.headers.get("content-length")
                    if declared_size is not None:
                        try:
                            if int(declared_size) > response_limit:
                                raise ExampleProxyError(502, "The example service response is too large")
                        except ValueError:
                            raise ExampleProxyError(502, "The example service returned an invalid response") from None
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > response_limit:
                            raise ExampleProxyError(502, "The example service response is too large")
                        chunks.append(chunk)
                    return httpx.Response(
                        response.status_code,
                        headers=response.headers,
                        content=b"".join(chunks),
                        request=response.request,
                    )
        except ExampleProxyError:
            raise
        except httpx.TimeoutException:
            raise ExampleProxyError(504, "The example service timed out") from None
        except httpx.HTTPError:
            raise ExampleProxyError(503, "The example service is unavailable") from None
        except (OSError, RuntimeError, ValueError):
            raise ExampleProxyError(503, "The example service is unavailable") from None
