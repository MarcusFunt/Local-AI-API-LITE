from __future__ import annotations

import json
import logging

import chat_voice_test_imports  # noqa: F401
import httpx
import pytest
from chat_voice_app.chat import PRESETS, ChatRequest, ChatService
from chat_voice_app.errors import ServiceError
from pydantic import ValidationError


def _service(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, **kwargs)

    return ChatService(client_factory=factory)


def _completion(text="A helpful answer", *, finish_reason="stop"):
    return httpx.Response(
        200,
        json={
            "model": "ddu-narrator",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 13},
        },
    )


@pytest.mark.parametrize(
    ("preset", "max_tokens", "thinking"),
    [("fast", 192, False), ("balanced", 384, False), ("quality", 768, True)],
)
def test_chat_uses_fixed_model_and_exact_preset_mapping(preset, max_tokens, thinking):
    captured = []

    def handler(request):
        captured.append(request)
        return _completion()

    result = _service(handler).complete(
        ChatRequest(preset=preset, messages=[{"role": "user", "content": "hello"}])
    )

    assert result.text == "A helpful answer"
    request = captured[0]
    assert request.url == "http://ddu-llm:8080/v1/chat/completions"
    assert request.headers["content-type"] == "application/json"
    payload = json.loads(request.content)
    assert payload == {
        "model": "ddu-narrator",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant. Be clear, direct, and honest about uncertainty."},
            {"role": "user", "content": "hello"},
        ],
        "stream": False,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": thinking},
    }


def test_chat_preserves_multiturn_order_and_rejects_client_model_options():
    captured = []
    service = _service(lambda request: captured.append(request) or _completion())
    messages = [
        {"role": "user", "content": "First"},
        {"role": "assistant", "content": "Reply"},
        {"role": "user", "content": "Follow up"},
    ]

    service.complete(ChatRequest(preset="balanced", messages=messages))
    payload = json.loads(captured[0].content)
    assert payload["messages"][1:] == messages
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(
            {"preset": "fast", "messages": messages, "model": "another-model"}
        )
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(
            {"preset": "fast", "messages": messages, "max_tokens": 999_999}
        )
    assert PRESETS["fast"].max_tokens == 192


@pytest.mark.parametrize(
    "value",
    [
        {"preset": "fast", "messages": []},
        {"preset": "unknown", "messages": [{"role": "user", "content": "hello"}]},
        {"preset": "fast", "messages": [{"role": "assistant", "content": "last"}]},
    ],
)
def test_chat_rejects_empty_invalid_and_malformed_turns(value):
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(value)


def test_chat_bounds_history_and_response_and_rejects_truncation():
    with pytest.raises(ValidationError):
        ChatRequest(
            preset="fast",
            messages=[{"role": "user", "content": "x" * 16_001}],
        )
    with pytest.raises(ValidationError):
        ChatRequest(
            preset="fast",
            messages=[{"role": "user", "content": "x"}] * 17,
        )
    with pytest.raises(ServiceError):
        _service(lambda _request: _completion(finish_reason="length")).complete(
            ChatRequest(preset="quality", messages=[{"role": "user", "content": "hello"}])
        )
    with pytest.raises(ServiceError):
        _service(lambda _request: _completion(text=" ")).complete(
            ChatRequest(preset="fast", messages=[{"role": "user", "content": "hello"}])
        )


def test_chat_timeout_and_http_failure_are_sanitized_and_logs_are_content_free(caplog):
    secret_prompt = "distinctive-user-prompt-9qz"
    secret_answer = "distinctive-model-answer-8zy"
    timeout_service = _service(
        lambda _request: (_ for _ in ()).throw(httpx.ReadTimeout(secret_prompt))
    )
    caplog.set_level(logging.INFO)
    with pytest.raises(ServiceError) as timeout_error:
        timeout_service.complete(
            ChatRequest(preset="fast", messages=[{"role": "user", "content": secret_prompt}])
        )
    assert timeout_error.value.status_code == 504
    error_service = _service(lambda _request: httpx.Response(503, text=secret_answer))
    with pytest.raises(ServiceError) as http_error:
        error_service.complete(
            ChatRequest(preset="fast", messages=[{"role": "user", "content": secret_prompt}])
        )
    assert http_error.value.status_code == 503
    assert secret_prompt not in caplog.text
    assert secret_answer not in caplog.text
