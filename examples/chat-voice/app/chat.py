from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import ServiceError
from .languages import Language

LOGGER = logging.getLogger("chat_voice.chat")
CHAT_URL = "http://ddu-llm:8080/v1/chat/completions"
MODEL_ALIAS = "ddu-narrator"
SYSTEM_PROMPT = "You are a helpful assistant. Be clear, direct, and honest about uncertainty."
LANGUAGE_PROMPTS: dict[Language, str] = {
    "da": SYSTEM_PROMPT + " Respond in Danish.",
    "en": SYSTEM_PROMPT + " Respond in English.",
}
MAX_CHAT_RESPONSE_BYTES = 256_000


@dataclass(frozen=True, slots=True)
class Preset:
    max_tokens: int
    enable_thinking: bool


PRESETS = {
    "fast": Preset(max_tokens=192, enable_thinking=False),
    "balanced": Preset(max_tokens=384, enable_thinking=False),
    "quality": Preset(max_tokens=768, enable_thinking=True),
}


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=16_000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    language: Language
    preset: Literal["fast", "balanced", "quality"]
    messages: list[ChatMessage] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_history(self) -> ChatRequest:
        if self.messages[-1].role != "user":
            raise ValueError("The final chat message must be from the user")
        if sum(len(message.content) for message in self.messages) > 64_000:
            raise ValueError("The chat history is too long")
        return self


@dataclass(frozen=True, slots=True)
class ChatResult:
    text: str
    elapsed_ms: int
    usage: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {"text": self.text, "elapsed_ms": self.elapsed_ms, "usage": self.usage}


class ChatService:
    def __init__(
        self,
        *,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self.client_factory = client_factory
        self.timeout = timeout or httpx.Timeout(connect=3, read=120, write=15, pool=3)
        self._capacity = threading.BoundedSemaphore(1)

    def complete(self, request: ChatRequest) -> ChatResult:
        if not self._capacity.acquire(blocking=False):
            raise ServiceError(429, "chat_saturated")
        started = time.perf_counter()
        try:
            preset = PRESETS[request.preset]
            payload = {
                "model": MODEL_ALIAS,
                "messages": [
                    {"role": "system", "content": LANGUAGE_PROMPTS[request.language]},
                    *[message.model_dump(mode="json") for message in request.messages],
                ],
                "stream": False,
                "max_tokens": preset.max_tokens,
                "chat_template_kwargs": {"enable_thinking": preset.enable_thinking},
            }
            response = self._post(payload)
            result = self._parse_response(response)
            elapsed_ms = min(600_000, max(0, int((time.perf_counter() - started) * 1000)))
            LOGGER.info(
                "chat completed model=%s preset=%s elapsed_ms=%d",
                MODEL_ALIAS,
                request.preset,
                elapsed_ms,
            )
            return ChatResult(result[0], elapsed_ms, result[1])
        except ServiceError as exc:
            LOGGER.warning("chat failed category=%s", exc.category)
            raise
        finally:
            self._capacity.release()

    def _post(self, payload: dict[str, object]) -> dict:
        try:
            with (
                self.client_factory(timeout=self.timeout, trust_env=False) as client,
                client.stream(
                    "POST",
                    CHAT_URL,
                    json=payload,
                    headers={"Accept-Encoding": "identity"},
                ) as response,
            ):
                if response.status_code < 200 or response.status_code >= 300:
                    raise ServiceError(503, "model_unavailable")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ServiceError(502, "model_invalid_response")
                content = _read_bounded(response, MAX_CHAT_RESPONSE_BYTES)
                try:
                    data = json.loads(content)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    raise ServiceError(502, "model_invalid_response") from None
        except ServiceError:
            raise
        except httpx.TimeoutException:
            raise ServiceError(504, "model_timeout") from None
        except httpx.HTTPError:
            raise ServiceError(503, "model_unavailable") from None
        except (OSError, RuntimeError, ValueError):
            raise ServiceError(503, "model_unavailable") from None
        if not isinstance(data, dict):
            raise ServiceError(502, "model_invalid_response")
        return data

    @staticmethod
    def _parse_response(data: dict) -> tuple[str, dict[str, int]]:
        if not isinstance(data, dict) or data.get("model") != MODEL_ALIAS:
            raise ServiceError(502, "model_invalid_response")
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ServiceError(502, "model_invalid_response")
        choice = choices[0]
        message = choice.get("message")
        if choice.get("finish_reason") != "stop" or not isinstance(message, dict):
            raise ServiceError(502, "model_incomplete_response")
        answer = message.get("content")
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 64_000:
            raise ServiceError(502, "model_invalid_response")
        usage = data.get("usage")
        safe_usage: dict[str, int] = {}
        if isinstance(usage, dict):
            for key in ("prompt_tokens", "completion_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100_000:
                    safe_usage[key] = value
        return answer, safe_usage


def _read_bounded(response: httpx.Response, limit: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise ServiceError(502, "upstream_response_too_large")
        except ValueError:
            raise ServiceError(502, "upstream_invalid_response") from None
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > limit:
            raise ServiceError(502, "upstream_response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)
