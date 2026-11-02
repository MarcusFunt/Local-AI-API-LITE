from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "retrieval_pipeline_benchmark", ROOT / "scripts" / "retrieval_pipeline_benchmark.py"
)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def test_summarize_reports_p50_p95_success_and_throughput():
    summary = benchmark.summarize_records(
        [
            {"mode": "pipeline", "success": True, "total_ms": 100.0, "stages_ms": {"embedding": 10.0}, "completion_tokens": 20},
            {"mode": "pipeline", "success": True, "total_ms": 200.0, "stages_ms": {"embedding": 20.0}, "completion_tokens": 30},
            {"mode": "pipeline", "success": False, "total_ms": 300.0, "stages_ms": {"embedding": 30.0}, "completion_tokens": 0},
        ]
    )["pipeline"]

    assert summary["requests"] == 3
    assert summary["successes"] == 2
    assert summary["errors"] == 1
    assert summary["total_ms"]["p50"] == 200
    assert summary["total_ms"]["p95"] == 300
    assert summary["completion_tokens"] == 50
    assert summary["completion_tokens_per_second"] > 0


def test_report_records_hashes_and_timing_without_fixture_content(tmp_path):
    fixture_path = ROOT / "scripts" / "fixtures" / "retrieval_pipeline_v1.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    writer = benchmark.ReportWriter(tmp_path)
    record = benchmark.sanitized_record(
        mode="pipeline",
        question=fixture["questions"][0],
        success=False,
        total_ms=12.5,
        stages_ms={"embedding": 2.0},
        error_category="upstream_timeout",
        completion_tokens=0,
    )
    writer.write([record], {"fixture_sha256": benchmark.sha256_file(fixture_path)})

    jsonl = (tmp_path / "results.jsonl").read_text(encoding="utf-8")
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "synthetic-01" in jsonl
    assert "Fixture SHA-256" in markdown
    assert fixture["questions"][0]["query"] not in jsonl
    assert fixture["questions"][0]["candidates"][0]["text"] not in jsonl
    assert "blue markører" not in jsonl
    assert "upstream_timeout" in jsonl


def test_fixture_is_versioned_and_explicitly_synthetic():
    fixture = json.loads((ROOT / "scripts" / "fixtures" / "retrieval_pipeline_v1.json").read_text(encoding="utf-8"))
    assert fixture["fixture_version"] == "synthetic-danish-inference-v1"
    assert len(fixture["questions"]) == 3
    assert all(question["candidates"] for question in fixture["questions"])
    assert "fictional" in fixture["system_prompt"]


def test_retrieval_service_readiness_requires_the_pinned_reported_revision(monkeypatch):
    alias, revision = benchmark.EXPECTED["embed"]
    monkeypatch.setattr(benchmark, "_http_get", lambda _url, _timeout: (200, {"status": "ready"}))
    monkeypatch.setattr(benchmark, "_model_records", lambda _url, _timeout: [{"id": alias, "revision": revision}])

    result = benchmark.verify_service("embed", "http://worker", 1)

    assert result["ready"] is True
    assert result["revision_verified"] is True
    assert result["reported_revision"] == revision

    monkeypatch.setattr(benchmark, "_model_records", lambda _url, _timeout: [{"id": alias, "revision": "other"}])
    with pytest.raises(benchmark.BenchmarkError, match="revision_mismatch"):
        benchmark.verify_service("embed", "http://worker", 1)


def test_narrator_liveness_accepts_empty_health_body_but_checks_alias(monkeypatch):
    alias, _revision = benchmark.EXPECTED["narrator"]
    monkeypatch.setattr(benchmark, "_http_get", lambda _url, _timeout: (200, {}))
    monkeypatch.setattr(benchmark, "_model_records", lambda _url, _timeout: [{"id": alias}])

    result = benchmark.verify_service("narrator", "http://worker", 1)

    assert result["ready"] is True
    assert result["revision_verified"] is False


def test_generation_disables_reasoning_and_validates_the_fixture_schema():
    fixture = json.loads((ROOT / "scripts" / "fixtures" / "retrieval_pipeline_v1.json").read_text(encoding="utf-8"))
    runner = benchmark.BenchmarkRunner(SimpleNamespace(
        embed_url="http://ddu-embed:8080",
        rerank_url="http://ddu-rerank:8080",
        narrator_url="http://ddu-llm:8080",
        max_duration_seconds=60,
    ))
    runner.fixture = fixture
    captured = {}

    def respond(method, url, payload):
        captured.update({"method": method, "url": url, "payload": payload})
        return {
            "choices": [{"message": {"content": '{"answer":"blue","evidence_ids":["synthetic-01-a"]}'}}],
            "usage": {"completion_tokens": 12},
        }

    runner.request = respond

    assert runner._generate(fixture["questions"][0], fixture["questions"][0]["candidates"]) == 12
    assert captured["payload"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured["url"] == "http://ddu-llm:8080/v1/chat/completions"


def test_generation_rejects_json_that_does_not_match_the_expected_shape():
    fixture = json.loads((ROOT / "scripts" / "fixtures" / "retrieval_pipeline_v1.json").read_text(encoding="utf-8"))
    runner = benchmark.BenchmarkRunner(SimpleNamespace(
        embed_url="http://ddu-embed:8080",
        rerank_url="http://ddu-rerank:8080",
        narrator_url="http://ddu-llm:8080",
        max_duration_seconds=60,
    ))
    runner.fixture = fixture
    runner.request = lambda *_args: {
        "choices": [{"message": {"content": '{"answer":"blue"}'}}],
        "usage": {"completion_tokens": 12},
    }

    with pytest.raises(benchmark.BenchmarkError, match="invalid_generation_response"):
        runner._generate(fixture["questions"][0], fixture["questions"][0]["candidates"])


def test_container_memory_parser_keeps_units_explicit():
    assert benchmark._parse_memory("1.5GiB") == 1.5 * 1024**3
    assert benchmark._parse_memory("unknown") is None
