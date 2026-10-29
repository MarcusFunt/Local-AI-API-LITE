from __future__ import annotations

from conftest import load_dashboard_module

from app.persistence import MetadataStore


def test_control_state_migrates_metadata_to_journal_schema(tmp_path):
    control_state = load_dashboard_module("control_state")
    metadata = MetadataStore(tmp_path / "dashboard.sqlite3")

    state = control_state.ControlStateStore(metadata)

    assert metadata.schema_version() == 3
    assert state.get_control_state()["desired_state"] == "stopped"


def test_two_activity_gate_instances_share_sqlite_lock(tmp_path):
    control_state = load_dashboard_module("control_state")
    path = tmp_path / "dashboard.sqlite3"
    first = control_state.ActivityGate(MetadataStore(path))
    second = control_state.ActivityGate(MetadataStore(path))

    assert first.acquire("inference", "operation-one", now=100, lease_seconds=30)
    assert not second.acquire("benchmark", "job-one", now=100, lease_seconds=30)
    first.release("operation-one")
    assert second.acquire("benchmark", "job-one", now=100, lease_seconds=30)


def test_expired_gate_lease_reconciles_before_reuse(tmp_path):
    control_state = load_dashboard_module("control_state")
    path = tmp_path / "dashboard.sqlite3"
    first = control_state.ActivityGate(MetadataStore(path))
    reconciled: list[dict] = []

    def reconcile(lease):
        reconciled.append(lease)
        return True

    second = control_state.ActivityGate(
        MetadataStore(path), expired_lease_reconciler=reconcile
    )

    assert first.acquire("inference", "abandoned", now=100, lease_seconds=5)
    assert second.acquire("inference", "replacement", now=106, lease_seconds=30)
    assert reconciled[0]["operation_id"] == "abandoned"
    assert reconciled[0]["kind"] == "inference"
    second.release("replacement")


def test_failed_expired_lease_reconciliation_keeps_gate_blocked(tmp_path):
    control_state = load_dashboard_module("control_state")
    path = tmp_path / "dashboard.sqlite3"
    first = control_state.ActivityGate(MetadataStore(path))
    rejecting = control_state.ActivityGate(
        MetadataStore(path), expired_lease_reconciler=lambda _lease: False
    )
    third = control_state.ActivityGate(MetadataStore(path))

    assert first.acquire("inference", "abandoned", now=100, lease_seconds=5)
    assert not rejecting.acquire("inference", "blocked", now=106, lease_seconds=30)
    assert not third.acquire("benchmark", "next", now=107, lease_seconds=30)


def test_benchmark_gate_rejects_concurrent_inference_mutation(tmp_path):
    control_state = load_dashboard_module("control_state")
    path = tmp_path / "dashboard.sqlite3"
    benchmark = control_state.ActivityGate(MetadataStore(path))
    inference = control_state.ActivityGate(MetadataStore(path))

    assert benchmark.acquire("benchmark", "benchmark-one", now=10, lease_seconds=60)
    assert not inference.acquire("inference", "start-one", now=11, lease_seconds=60)
