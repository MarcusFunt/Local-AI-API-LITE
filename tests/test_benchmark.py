from __future__ import annotations

import json

from scripts import benchmark


def test_summary_reports_latency_queue_time_and_token_throughput():
    results = [
        {
            "case": "history",
            "model": "ddu-narrator",
            "ok": True,
            "elapsed_seconds": 1.0,
            "completion_tokens": 10,
            "queue_time_ms": 4.0,
            "time_to_first_token_ms": 100.0,
        },
        {
            "case": "medical",
            "model": "ddu-narrator",
            "ok": True,
            "elapsed_seconds": 3.0,
            "completion_tokens": 20,
            "queue_time_ms": 8.0,
            "time_to_first_token_ms": 200.0,
        },
    ]

    summary = benchmark.summarize_results(results, concurrency=2, context_length=4096, wall_seconds=2)

    assert summary["request_count"] == 2
    assert summary["success_rate"] == 1.0
    assert summary["p50_latency_seconds"] == 1.0
    assert summary["p95_latency_seconds"] == 3.0
    assert summary["p50_queue_time_ms"] == 4.0
    assert summary["p95_time_to_first_token_ms"] == 200.0
    assert summary["output_tokens"] == 30
    assert summary["tokens_per_second"] == 15.0


def test_jsonl_writer_keeps_only_sanitized_request_and_summary_fields(tmp_path):
    request = {
        "kind": "request",
        "case": "medical",
        "model": "ddu-narrator",
        "ok": True,
        "elapsed_seconds": 0.5,
        "completion_tokens": 5,
        "prompt": "private prompt",
        "content": "private model output",
    }
    summary = benchmark.summarize_results([request], concurrency=1, context_length=4096, wall_seconds=0.5)
    path = tmp_path / "metrics.jsonl"

    benchmark.write_jsonl(path, [request, summary])

    output = path.read_text(encoding="utf-8")
    assert "private prompt" not in output
    assert "private model output" not in output
    records = [json.loads(line) for line in output.splitlines()]
    assert records[0]["kind"] == "request"
    assert records[1]["kind"] == "summary"
    assert "p95_latency_seconds" in records[1]


def test_percentile_uses_nearest_rank_and_handles_empty_inputs():
    assert benchmark.percentile([5.0, 1.0, 3.0], 50) == 3.0
    assert benchmark.percentile([5.0, 1.0, 3.0], 95) == 5.0
    assert benchmark.percentile([], 95) is None
