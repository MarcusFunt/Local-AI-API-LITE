#!/usr/bin/env python3
"""Run bounded concurrent requests and write sanitized JSONL metrics."""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

try:
    from .shared_inference_smoke import CASES, positive_number, run_case
except ImportError:  # direct script execution in a throwaway client container
    from shared_inference_smoke import CASES, positive_number, run_case


REQUEST_FIELDS = {
    "kind",
    "timestamp_utc",
    "context_length",
    "concurrency",
    "case",
    "model",
    "outcome",
    "elapsed_seconds",
    "client_queue_wait_seconds",
    "queue_time_ms",
    "time_to_first_token_ms",
    "tokens_per_second",
    "completion_tokens",
    "request_count",
    "retry_count",
    "exception_class",
    "error",
    "http_status",
}
SUMMARY_FIELDS = {
    "kind",
    "timestamp_utc",
    "context_length",
    "concurrency",
    "request_count",
    "success_count",
    "success_rate",
    "wall_seconds",
    "p50_latency_seconds",
    "p95_latency_seconds",
    "p50_client_queue_wait_seconds",
    "p95_client_queue_wait_seconds",
    "p50_queue_time_ms",
    "p95_queue_time_ms",
    "p50_time_to_first_token_ms",
    "p95_time_to_first_token_ms",
    "output_tokens",
    "tokens_per_second",
}


def percentile(values: list[float], percent: float) -> float | None:
    """Return a rounded nearest-rank percentile."""
    if not 0 < percent <= 100:
        raise ValueError("percent must be greater than 0 and at most 100")
    clean = sorted(float(value) for value in values if value is not None)
    if not clean:
        return None
    index = max(0, min(len(clean) - 1, int((percent / 100) * len(clean) + 0.999999) - 1))
    return round(clean[index], 3)


def _numeric_values(records: list[dict], field: str) -> list[float]:
    values = []
    for record in records:
        value = record.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            values.append(float(value))
    return values


def summarize_results(
    results: list[dict],
    concurrency: int,
    context_length: int,
    wall_seconds: float,
) -> dict:
    succeeded = [result for result in results if result.get("ok") is True]
    output_tokens = sum(
        result.get("completion_tokens", 0)
        for result in succeeded
        if isinstance(result.get("completion_tokens"), int)
        and not isinstance(result.get("completion_tokens"), bool)
        and result["completion_tokens"] >= 0
    )
    elapsed = _numeric_values(results, "elapsed_seconds")
    client_queue = _numeric_values(results, "client_queue_wait_seconds")
    server_queue = _numeric_values(succeeded, "queue_time_ms")
    ttft = _numeric_values(succeeded, "time_to_first_token_ms")
    return {
        "kind": "summary",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "context_length": context_length,
        "concurrency": concurrency,
        "request_count": len(results),
        "success_count": len(succeeded),
        "success_rate": round(len(succeeded) / len(results), 3) if results else 0.0,
        "wall_seconds": round(max(0.0, wall_seconds), 3),
        "p50_latency_seconds": percentile(elapsed, 50),
        "p95_latency_seconds": percentile(elapsed, 95),
        "p50_client_queue_wait_seconds": percentile(client_queue, 50),
        "p95_client_queue_wait_seconds": percentile(client_queue, 95),
        "p50_queue_time_ms": percentile(server_queue, 50),
        "p95_queue_time_ms": percentile(server_queue, 95),
        "p50_time_to_first_token_ms": percentile(ttft, 50),
        "p95_time_to_first_token_ms": percentile(ttft, 95),
        "output_tokens": output_tokens,
        "tokens_per_second": round(output_tokens / wall_seconds, 3) if wall_seconds > 0 else None,
    }


def sanitize_request_record(result: dict, concurrency: int, context_length: int, kind: str = "request") -> dict:
    record = {
        "kind": kind,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "context_length": context_length,
        "concurrency": concurrency,
        "case": result.get("case"),
        "model": result.get("model"),
        "outcome": "success" if result.get("ok") is True else "failure",
    }
    for field in REQUEST_FIELDS - set(record):
        if field in result:
            record[field] = result[field]
    return record


def write_jsonl(path: str | Path, records: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            allowed = SUMMARY_FIELDS if record.get("kind") == "summary" else REQUEST_FIELDS
            clean = {key: value for key, value in record.items() if key in allowed}
            stream.write(json.dumps(clean, ensure_ascii=False, separators=(",", ":")) + "\n")


def _run_batch(
    base_url: str,
    model: str,
    concurrency: int,
    request_count: int,
    context_length: int,
    max_tokens: int,
    timeout: float,
    max_retries: int,
) -> tuple[list[dict], dict]:
    def execute(case: str, submitted_at: float) -> dict:
        dispatch_at = time.monotonic()
        result = run_case(
            base_url,
            model,
            case,
            timeout,
            max_tokens=max_tokens,
            max_retries=max_retries,
        )
        result["client_queue_wait_seconds"] = round(dispatch_at - submitted_at, 3)
        return result

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = []
        case_names = list(CASES)
        for index in range(request_count):
            case = case_names[index % len(case_names)]
            futures.append(pool.submit(execute, case, time.monotonic()))
        results = [future.result() for future in futures]
    wall_seconds = time.monotonic() - started
    records = [sanitize_request_record(result, concurrency, context_length) for result in results]
    summary = summarize_results(results, concurrency, context_length, wall_seconds)
    return records, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://ddu-llm:8080")
    parser.add_argument("--model", default="ddu-narrator")
    parser.add_argument("--context-length", type=int, default=4096)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--requests-per-concurrency", type=int, default=20)
    parser.add_argument("--warmup-per-case", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--timeout", type=positive_number, default=120.0)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--output", type=Path, default=Path("benchmark-results.jsonl"))
    args = parser.parse_args(argv)
    if not 1 <= args.context_length <= 262144:
        parser.error("--context-length must be between 1 and 262144")
    if not args.concurrency or any(not 1 <= value <= 16 for value in args.concurrency):
        parser.error("--concurrency values must be between 1 and 16")
    if not 1 <= args.requests_per_concurrency <= 200:
        parser.error("--requests-per-concurrency must be between 1 and 200")
    if not 0 <= args.warmup_per_case <= 4:
        parser.error("--warmup-per-case must be between 0 and 4")
    if not 1 <= args.max_tokens <= 4096 or not 0 <= args.max_retries <= 5:
        parser.error("max tokens must be 1-4096 and max retries 0-5")

    records = []
    summaries = []
    for case in CASES:
        for _ in range(args.warmup_per_case):
            result = run_case(
                args.base_url,
                args.model,
                case,
                args.timeout,
                max_tokens=args.max_tokens,
                max_retries=args.max_retries,
            )
            records.append(
                sanitize_request_record(result, 1, args.context_length, kind="warmup")
            )
            if not result.get("ok"):
                write_jsonl(args.output, records)
                print(json.dumps({"ok": False, "phase": "warmup", "output": str(args.output)}))
                return 1

    for concurrency in args.concurrency:
        batch_records, summary = _run_batch(
            args.base_url,
            args.model,
            concurrency,
            args.requests_per_concurrency,
            args.context_length,
            args.max_tokens,
            args.timeout,
            args.max_retries,
        )
        records.extend(batch_records)
        records.append(summary)
        summaries.append(summary)
    write_jsonl(args.output, records)
    print(json.dumps({"ok": all(s["success_rate"] == 1 for s in summaries), "output": str(args.output), "summaries": summaries}))
    return 0 if all(s["success_rate"] == 1 for s in summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
