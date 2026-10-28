#!/usr/bin/env python3
"""Exercise the shared inference API with independent synthetic workloads."""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib import error, request

CASES = {
    "history": {
        "field": "language",
        "value": "da",
        "prompt": (
            "Du er diplomat ved fredsforhandlingerne i Versailles i 1919. "
            "Svar med en kort replik på dansk om fred. Dette er et syntetisk testprompt."
        ),
    },
    "medical": {
        "field": "speaker",
        "value": "examiner",
        "prompt": (
            "This is a purely fictional medical-examiner exercise. Ask a candidate "
            "to introduce themself in one short sentence. No real patient is involved; "
            "do not make a diagnosis."
        ),
    },
}
RETRYABLE_STATUSES = {429, 503}


class InvalidResponse(ValueError):
    """The completion did not satisfy the served-model contract."""


def build_payload(model: str, case: str, max_tokens: int, disable_thinking: bool) -> dict:
    if case not in CASES:
        raise ValueError("unknown workload")
    spec = CASES[case]
    schema = {
        "type": "object",
        "properties": {
            spec["field"]: {"type": "string", "enum": [spec["value"]]},
            "reply": {"type": "string", "minLength": 1, "maxLength": 600},
        },
        "required": [spec["field"], "reply"],
        "additionalProperties": False,
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": spec["prompt"]}],
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": f"{case}_probe", "strict": True, "schema": schema},
        },
    }
    if disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


def validate_response(document: object, case: str, expected_model: str) -> dict:
    if case not in CASES:
        raise InvalidResponse("unknown workload")
    if not isinstance(document, dict) or document.get("model") != expected_model:
        raise InvalidResponse("unexpected model alias")
    choices = document.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise InvalidResponse("missing completion choice")
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        raise InvalidResponse("completion did not stop normally")
    message = choice.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise InvalidResponse("completion content is not text")
    try:
        result = json.loads(message["content"])
    except (json.JSONDecodeError, TypeError):
        raise InvalidResponse("completion content is not JSON") from None

    spec = CASES[case]
    if not isinstance(result, dict) or set(result) != {spec["field"], "reply"}:
        raise InvalidResponse("completion fields do not match schema")
    if result[spec["field"]] != spec["value"]:
        raise InvalidResponse("completion workload marker is incorrect")
    reply = result["reply"]
    if not isinstance(reply, str) or not reply.strip() or len(reply) > 600:
        raise InvalidResponse("completion reply is invalid")
    return result


def _bounded_urlopen(http_request: request.Request, timeout: float) -> tuple[int, bytes]:
    """Bound the caller's wait even if name resolution stalls in urllib."""
    completed: queue.Queue[tuple[int | None, bytes | None, Exception | None]] = queue.Queue(1)

    def send() -> None:
        try:
            with request.urlopen(http_request, timeout=timeout) as response:
                completed.put((response.status, response.read(), None))
        except (error.URLError, TimeoutError, OSError, ValueError) as exc:
            completed.put((None, None, exc))

    threading.Thread(target=send, daemon=True).start()
    try:
        status, body, failure = completed.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError() from None
    if failure is not None:
        raise failure
    return int(status), body or b""


def sanitize_failure(
    case: str,
    model: str,
    exception: Exception,
    elapsed_seconds: float,
    attempts: int,
    *,
    category: str = "request_failed",
    http_status: int | None = None,
) -> dict:
    """Return metrics only; never include prompts, response bodies, or exception text."""
    record = {
        "case": case,
        "model": model,
        "ok": False,
        "error": category,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "attempts": attempts,
        "request_count": attempts,
        "retry_count": max(0, attempts - 1),
        "exception_class": type(exception).__name__,
    }
    if http_status is not None:
        record["http_status"] = http_status
    return record


def _failed_result(
    case: str,
    model: str,
    started: float,
    attempts: int,
    exception: Exception,
    category: str,
    http_status: int | None = None,
) -> dict:
    return sanitize_failure(
        case,
        model,
        exception,
        time.monotonic() - started,
        attempts,
        category=category,
        http_status=http_status,
    )


def _safe_metric(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return round(number, 3)


def run_case(
    base_url: str,
    model: str,
    case: str,
    timeout: float,
    disable_thinking: bool = False,
    max_tokens: int = 180,
    max_retries: int = 2,
    retry_delay: float = 0.5,
) -> dict:
    started = time.monotonic()
    attempts = 0
    if not 1 <= max_tokens <= 4096 or not 0 <= max_retries <= 5:
        return _failed_result(case, model, started, attempts, ValueError(), "invalid_configuration")
    try:
        payload = build_payload(model, case, max_tokens, disable_thinking)
    except ValueError as exc:
        return _failed_result(case, model, started, attempts, exc, "invalid_configuration")

    http_request = request.Request(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for retry_index in range(max_retries + 1):
        attempts += 1
        try:
            status, body = _bounded_urlopen(http_request, timeout)
            if status != 200:
                raise InvalidResponse("unexpected HTTP status")
            document = json.loads(body)
            validate_response(document, case, model)
            usage = document.get("usage") if isinstance(document, dict) else None
            metrics = document.get("metrics") if isinstance(document, dict) else None
            completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
            if isinstance(completion_tokens, bool) or not isinstance(completion_tokens, int) or completion_tokens < 0:
                completion_tokens = None
            metrics = metrics if isinstance(metrics, dict) else {}
            return {
                "case": case,
                "model": model,
                "ok": True,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "attempts": attempts,
                "request_count": attempts,
                "retry_count": attempts - 1,
                "exception_class": None,
                "completion_tokens": completion_tokens,
                "queue_time_ms": _safe_metric(metrics.get("queue_time_ms")),
                "time_to_first_token_ms": _safe_metric(metrics.get("time_to_first_token_ms")),
                "tokens_per_second": _safe_metric(metrics.get("tokens_per_second")),
            }
        except error.HTTPError as exc:
            if exc.code in RETRYABLE_STATUSES and retry_index < max_retries:
                time.sleep(min(max(0.0, retry_delay) * (retry_index + 1), 2.0))
                continue
            return _failed_result(case, model, started, attempts, exc, "http_error", exc.code)
        except TimeoutError as exc:
            return _failed_result(case, model, started, attempts, exc, "timeout")
        except error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                return _failed_result(case, model, started, attempts, reason, "timeout")
            return _failed_result(case, model, started, attempts, exc, "connection_error")
        except (InvalidResponse, json.JSONDecodeError, UnicodeError) as exc:
            return _failed_result(case, model, started, attempts, exc, "invalid_response")
        except ValueError as exc:
            return _failed_result(case, model, started, attempts, exc, "connection_error")
        except OSError as exc:
            return _failed_result(case, model, started, attempts, exc, "connection_error")
    return _failed_result(case, model, started, attempts, RuntimeError(), "request_failed")


def positive_number(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive number") from exc
    if not 0 < number <= 600:
        raise argparse.ArgumentTypeError("must be greater than 0 and at most 600")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("LLM_BASE_URL", "http://ddu-llm:8080"))
    parser.add_argument("--model", default=os.getenv("LLM_MODEL_ALIAS", "ddu-narrator"))
    parser.add_argument("--case", choices=["all", *CASES], default="all")
    parser.add_argument("--timeout", type=positive_number, default=120.0, help="per-request timeout")
    parser.add_argument("--max-tokens", type=int, default=180)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--disable-thinking", action="store_true")
    parser.add_argument("--concurrent", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.max_tokens <= 4096:
        parser.error("--max-tokens must be between 1 and 4096")
    if not 0 <= args.max_retries <= 5:
        parser.error("--max-retries must be between 0 and 5")
    selected = list(CASES) if args.case == "all" else [args.case]
    started = time.monotonic()

    def run(case: str) -> dict:
        return run_case(
            args.base_url,
            args.model,
            case,
            args.timeout,
            args.disable_thinking,
            args.max_tokens,
            args.max_retries,
        )

    if args.concurrent and len(selected) > 1:
        with ThreadPoolExecutor(max_workers=len(selected)) as pool:
            results = list(pool.map(run, selected))
    else:
        results = [run(case) for case in selected]
    print(json.dumps({"results": results, "wall_seconds": round(time.monotonic() - started, 3)}))
    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
