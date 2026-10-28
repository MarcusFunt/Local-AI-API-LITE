#!/usr/bin/env python3
"""Check vLLM HTTP readiness and the expected API model alias."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib import error, request

try:
    from .shared_inference_smoke import (
        RETRYABLE_STATUSES,
        _bounded_urlopen,
        positive_number,
    )
except ImportError:  # direct script execution in a throwaway client container
    from shared_inference_smoke import (
        RETRYABLE_STATUSES,
        _bounded_urlopen,
        positive_number,
    )


class ServerCheckError(RuntimeError):
    """A sanitized readiness or alias check failure."""

    def __init__(self, category: str, exception_class: str, http_status: int | None = None):
        super().__init__(category)
        self.category = category
        self.exception_class = exception_class
        self.http_status = http_status


def _get_with_retry(url: str, timeout: float, max_retries: int, retry_delay: float) -> tuple[bytes, int]:
    attempts = 0
    for retry_index in range(max_retries + 1):
        attempts += 1
        http_request = request.Request(url, method="GET")
        try:
            status, body = _bounded_urlopen(http_request, timeout)
            if status == 200:
                return body, attempts
            if status in RETRYABLE_STATUSES and retry_index < max_retries:
                time.sleep(min(max(0.0, retry_delay) * (retry_index + 1), 2.0))
                continue
            raise ServerCheckError("http_error", "HTTPResponse", status)
        except error.HTTPError as exc:
            if exc.code in RETRYABLE_STATUSES and retry_index < max_retries:
                time.sleep(min(max(0.0, retry_delay) * (retry_index + 1), 2.0))
                continue
            raise ServerCheckError("http_error", "HTTPError", exc.code) from None
        except TimeoutError:
            raise ServerCheckError("timeout", "TimeoutError") from None
        except error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise ServerCheckError("timeout", type(reason).__name__) from None
            raise ServerCheckError("connection_error", "URLError") from None
        except OSError as exc:
            raise ServerCheckError("connection_error", type(exc).__name__) from None
        except ValueError:
            raise ServerCheckError("connection_error", "ValueError") from None
    raise ServerCheckError("request_failed", "RuntimeError")


def check_server(
    base_url: str,
    model: str,
    timeout: float = 10,
    max_retries: int = 3,
    retry_delay: float = 0.5,
) -> dict:
    if not 0 <= max_retries <= 5:
        raise ValueError("max_retries must be between 0 and 5")
    base_url = base_url.rstrip("/")
    _health_body, health_attempts = _get_with_retry(
        f"{base_url}/health", timeout, max_retries, retry_delay
    )
    model_body, model_attempts = _get_with_retry(
        f"{base_url}/v1/models", timeout, max_retries, retry_delay
    )
    try:
        document = json.loads(model_body)
        models = document["data"]
        model_ids = [
            entry["id"]
            for entry in models
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        ]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise ServerCheckError("invalid_models_response", "ValueError") from None
    if model not in model_ids:
        raise ServerCheckError("model_alias_mismatch", "ValueError")
    return {
        "ok": True,
        "health": "ok",
        "model": model,
        "requests": health_attempts + model_attempts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("LLM_BASE_URL", "http://ddu-llm:8080"))
    parser.add_argument("--model", default=os.getenv("LLM_MODEL_ALIAS", "ddu-narrator"))
    parser.add_argument("--timeout", type=positive_number, default=10.0)
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        result = check_server(args.base_url, args.model, args.timeout, args.max_retries)
    except ServerCheckError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": exc.category,
                    "exception_class": exc.exception_class,
                    "http_status": exc.http_status,
                }
            )
        )
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
