from __future__ import annotations

import json

import pytest

from scripts import shared_inference_smoke as smoke


def completion(content: str, *, model: str = "ddu-narrator", finish_reason: str = "stop"):
    return {
        "model": model,
        "choices": [{"finish_reason": finish_reason, "message": {"content": content}}],
    }


def test_history_and_fictional_examiner_have_distinct_strict_schemas():
    history = smoke.build_payload("ddu-narrator", "history", 120, False)
    examiner = smoke.build_payload("ddu-narrator", "medical", 120, False)

    history_schema = history["response_format"]["json_schema"]["schema"]
    examiner_schema = examiner["response_format"]["json_schema"]["schema"]
    assert history_schema != examiner_schema
    assert history_schema["required"] == ["language", "reply"]
    assert examiner_schema["required"] == ["speaker", "reply"]
    assert history_schema["additionalProperties"] is False
    assert examiner_schema["additionalProperties"] is False
    assert "chat_template_kwargs" not in history
    examiner_prompt = examiner["messages"][0]["content"].lower()
    assert "purely fictional" in examiner_prompt
    assert "no real patient" in examiner_prompt


@pytest.mark.parametrize(
    ("document", "case"),
    [
        (completion('{"language":"da","reply":"Fred."}'), "history"),
        (completion('{"speaker":"examiner","reply":"Please introduce yourself."}'), "medical"),
    ],
)
def test_validator_accepts_each_workloads_own_schema(document, case):
    result = smoke.validate_response(document, case, "ddu-narrator")
    assert result["reply"]


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"model": "ddu-narrator", "choices": []},
        completion('{"language":"da","reply":"Fred."}', model="other-model"),
        {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]},
        {"model": "ddu-narrator", "choices": [{"message": {"content": '{"language":"da","reply":"Fred."}'}}]},
        completion('{"language":"da","reply":"Fred."}', finish_reason="length"),
        completion(""),
        completion("not json"),
        completion("[]"),
        completion('{"language":"en","reply":"Peace."}'),
        completion('{"language":"da"}'),
        completion('{"language":"da","reply":"Fred.","extra":true}'),
        completion('{"speaker":"patient","reply":"Hello."}'),
        completion('{"speaker":"examiner","reply":"Hello.","diagnosis":"none"}'),
        completion('{"language":"da","reply":"  "}'),
        completion('{"language":"da","reply":7}'),
    ],
)
def test_validator_rejects_wrong_alias_finish_reason_or_schema(document):
    with pytest.raises(smoke.InvalidResponse):
        smoke.validate_response(document, "history", "ddu-narrator")


def test_optional_thinking_flag_is_sent_only_when_requested():
    plain = smoke.build_payload("ddu-narrator", "history", 120, False)
    no_thinking = smoke.build_payload("ddu-narrator", "history", 120, True)

    assert "chat_template_kwargs" not in plain
    assert no_thinking["chat_template_kwargs"] == {"enable_thinking": False}


def test_sanitized_failure_record_has_no_prompt_output_or_error_body():
    prompt = smoke.CASES["medical"]["prompt"]
    output = "Please introduce yourself to the fictional examiner."
    failure = smoke.sanitize_failure("medical", "ddu-narrator", RuntimeError("private detail"), 0.1, 2)

    serialized = json.dumps(failure)
    assert prompt not in serialized
    assert output not in serialized
    assert "private detail" not in serialized
    assert failure["case"] == "medical"
    assert failure["model"] == "ddu-narrator"
    assert failure["attempts"] == 2
    assert failure["exception_class"] == "RuntimeError"
