from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest
from conftest import load_dashboard_module
from fakes import FakeJobCommandAdapter

from app.persistence import MetadataStore


def make_runner(tmp_path, adapter=None, *, max_files=30, max_bytes=50_000_000):
    jobs = load_dashboard_module("jobs")
    control_state = load_dashboard_module("control_state")
    metadata = MetadataStore(tmp_path / "metadata.sqlite3")
    gate = control_state.ActivityGate(metadata)
    command = adapter or FakeJobCommandAdapter()
    artifacts = jobs.JobArtifactStore(
        tmp_path / "artifacts", max_files=max_files, max_total_bytes=max_bytes
    )
    runner = jobs.JobRunner(gate, command, artifacts)
    return jobs, gate, command, artifacts, runner


def wait_for_terminal(runner, job_id, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = runner.status(job_id)
        if record["state"] in {"completed", "failed", "cancelled", "rejected"}:
            return record
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not finish before the test deadline")


def test_unknown_job_and_arbitrary_arguments_are_rejected(tmp_path):
    jobs, _gate, _command, _artifacts, runner = make_runner(tmp_path)

    with pytest.raises(jobs.JobValidationError):
        runner.start("shell", {"command": "docker compose down"})
    with pytest.raises(jobs.JobValidationError):
        runner.start("server-readiness", {"command": "python attacker.py"})


def test_numeric_bounds_and_concurrency_default_to_one_or_two(tmp_path):
    jobs, _gate, command, _artifacts, runner = make_runner(tmp_path)

    created = runner.start("performance-benchmark", {})
    command.started.wait(1)
    record = wait_for_terminal(runner, created["job_id"])
    assert record["options"]["concurrency"] in {1, 2}
    assert command.calls[0]["options"]["concurrency"] in {1, 2}
    with pytest.raises(jobs.JobValidationError):
        runner.start("performance-benchmark", {"requests_per_concurrency": 0})
    with pytest.raises(jobs.JobValidationError):
        runner.start("performance-benchmark", {"concurrency": 4})


def test_only_one_mutating_or_benchmark_job_runs_at_once(tmp_path):
    command = FakeJobCommandAdapter()
    command.block = True
    command.stdout = json.dumps(
        {
            "ok": True,
            "summaries": [
                {
                    "request_count": 10,
                    "success_count": 10,
                    "success_rate": 1,
                    "p50_latency_seconds": 1.2,
                    "p95_latency_seconds": 2.3,
                }
            ],
        }
    )
    _jobs, _gate, _command, _artifacts, runner = make_runner(tmp_path, command)
    first = runner.start("performance-benchmark", {})
    assert command.started.wait(1)

    second = runner.start("api-smoke", {})
    assert second["state"] == "rejected"
    assert second["error_category"] == "activity_conflict"
    assert len(command.calls) == 1

    command.release.set()
    assert wait_for_terminal(runner, first["job_id"])["state"] == "completed"


def test_inference_operation_gate_rejects_concurrent_benchmark(tmp_path):
    _jobs, gate, command, _artifacts, runner = make_runner(tmp_path)
    assert gate.acquire("inference", "inference-op", time.time(), 60)

    job = runner.start("performance-benchmark", {})

    assert job["state"] == "rejected"
    assert command.calls == []
    assert gate.current()["operation_id"] == "inference-op"


def test_benchmark_profile_must_match_the_observed_inference_profile(tmp_path):
    command = FakeJobCommandAdapter()
    command.active_profile = "baseline-8k-4"
    _jobs, _gate, _command, _artifacts, runner = make_runner(tmp_path, command)

    created = runner.start("performance-benchmark", {"profile_id": "baseline-4k-2"})
    record = wait_for_terminal(runner, created["job_id"])

    assert record["state"] == "failed"
    assert record["error_category"] == "profile_mismatch"
    assert command.calls == []


def test_job_timeout_and_cancel_are_recorded(tmp_path):
    command = FakeJobCommandAdapter()
    command.block = True
    _jobs, _gate, _command, _artifacts, runner = make_runner(tmp_path, command)
    timed_out = runner.start("server-readiness", {"timeout_seconds": 1})
    assert command.started.wait(1)
    timeout_record = wait_for_terminal(runner, timed_out["job_id"], timeout=2)
    assert timeout_record["state"] == "failed"
    assert timeout_record["error_category"] == "timeout"

    command.block = True
    command.started.clear()
    command.release.clear()
    cancelled = runner.start("api-smoke", {})
    assert command.started.wait(1)
    assert runner.cancel(cancelled["job_id"]) is True
    cancel_record = wait_for_terminal(runner, cancelled["job_id"])
    assert cancel_record["state"] == "cancelled"
    assert cancel_record["error_category"] == "cancelled_by_operator"


def test_unconfirmed_container_stop_keeps_gate_blocked_until_reconciled(tmp_path):
    class UnconfirmedStopAdapter:
        def __init__(self):
            self.started = threading.Event()
            self.container_stopped = False
            self.reconciliation_calls = []

        def run(self, _job_id, _options, *, artifact_path, timeout_seconds, cancel_event):
            self.started.set()
            cancel_event.wait(timeout=2)
            return SimpleNamespace(return_code=130, stdout="{}", container_stopped=False)

        def ensure_container_stopped(self, job_id):
            self.reconciliation_calls.append(job_id)
            return self.container_stopped

    command = UnconfirmedStopAdapter()
    _jobs, gate, _command, _artifacts, runner = make_runner(tmp_path / "runner", command)
    created = runner.start("server-readiness", {})
    assert command.started.wait(1)

    assert runner.cancel(created["job_id"]) is True
    record = wait_for_terminal(runner, created["job_id"])

    assert record["state"] == "failed"
    assert record["error_category"] == "runtime_unavailable"
    lease = gate.current()
    assert lease is not None
    assert lease["operation_id"] == created["job_id"]
    assert lease["kind"] == "job"
    assert lease["lease_until"] is None
    assert lease["status"] == "reconciliation_failed"

    assert gate.acquire("inference", "attempt-one", now=time.time(), lease_seconds=60) is False
    blocked = gate.current()
    assert blocked is not None
    assert blocked["operation_id"] == created["job_id"]
    assert blocked["status"] == "reconciliation_failed"

    command.container_stopped = True
    assert gate.acquire("inference", "attempt-two", now=time.time(), lease_seconds=60) is True
    assert command.reconciliation_calls == [created["job_id"], created["job_id"]]


def test_job_output_never_contains_prompt_or_completion(tmp_path):
    command = FakeJobCommandAdapter()
    command.stdout = json.dumps(
        {
            "ok": False,
            "results": [
                {
                    "case": "history",
                    "model": "ddu-narrator",
                    "ok": False,
                    "error": "connection_error",
                    "prompt": "SECRET_PROMPT",
                    "completion": "SECRET_COMPLETION",
                }
            ],
            "completion": "SECRET_COMPLETION",
        }
    )
    _jobs, _gate, _command, artifacts, runner = make_runner(tmp_path, command)

    created = runner.start("api-smoke", {})
    record = wait_for_terminal(runner, created["job_id"])
    stored = json.dumps(record) + artifacts.read_text(record["artifact_id"])

    assert record["state"] == "failed"
    assert "SECRET_PROMPT" not in stored
    assert "SECRET_COMPLETION" not in stored


def test_output_size_and_artifact_retention_are_bounded(tmp_path):
    command = FakeJobCommandAdapter()
    jobs, _gate, _command, artifacts, runner = make_runner(
        tmp_path, command, max_files=1, max_bytes=1024
    )
    command.stdout = "x" * (jobs.MAX_OUTPUT_BYTES + 1)
    oversized = runner.start("server-readiness", {})
    oversized_record = wait_for_terminal(runner, oversized["job_id"])
    assert oversized_record["state"] == "failed"
    assert oversized_record["error_category"] == "output_too_large"
    assert oversized_record["artifact_id"] is None

    command.stdout = '{"ok":true,"health":"ok","model":"ddu-narrator","requests":2}'
    first = runner.start("server-readiness", {})
    first_record = wait_for_terminal(runner, first["job_id"])
    second = runner.start("server-readiness", {})
    second_record = wait_for_terminal(runner, second["job_id"])
    assert len(artifacts.list_artifacts()) == 1
    assert artifacts.list_artifacts()[0].name == second_record["artifact_id"]
    assert runner.status(first_record["job_id"])["artifact_id"] is None


def test_terminal_job_state_waits_for_activity_gate_release(tmp_path, monkeypatch):
    _jobs, gate, _command, _artifacts, runner = make_runner(tmp_path)
    release_started = threading.Event()
    allow_release = threading.Event()
    original_release = gate.release

    def delayed_release(job_id):
        release_started.set()
        if not allow_release.wait(timeout=2):
            raise AssertionError("test did not release the activity gate")
        original_release(job_id)

    monkeypatch.setattr(gate, "release", delayed_release)
    created = runner.start("server-readiness", {})

    assert release_started.wait(timeout=2)
    try:
        assert runner.status(created["job_id"])["state"] == "running"
    finally:
        allow_release.set()

    assert wait_for_terminal(runner, created["job_id"])["state"] == "completed"
