from __future__ import annotations

from pathlib import Path

from conftest import load_dashboard_module


class SettingsStub:
    def load_retrieval_compose_root(self):
        return Path("C:/explicit/retrieval/project")


class RunnerStub:
    def __init__(self):
        self.calls = []
        self.failure = None

    def validate_project(self):
        self.calls.append(("validate",))
        return Path("C:/explicit/retrieval/project")

    def start(self, service, heartbeat=None):
        self.calls.append(("start", service))
        if heartbeat:
            heartbeat()
        if self.failure:
            raise self.failure
        return {"service": service, "runtime_state": "running", "model_id": f"ddu-{service}"}

    def stop(self, service):
        self.calls.append(("stop", service))
        return {"service": service, "runtime_state": "stopped"}

    def restart(self, service, heartbeat=None):
        self.calls.append(("restart", service))
        if heartbeat:
            heartbeat()
        if self.failure:
            raise self.failure
        return {"service": service, "runtime_state": "running"}

    def inspect_service(self, service):
        self.calls.append(("inspect", service))
        return {"service": service, "runtime_state": "running"}


def make_coordinator(tmp_path: Path):
    auth = load_dashboard_module("auth")
    control_state = load_dashboard_module("control_state")
    persistence = load_dashboard_module("persistence")
    secrets_store = load_dashboard_module("secrets_store")
    retrieval_operations = load_dashboard_module("retrieval_operations")
    metadata = persistence.MetadataStore(tmp_path / "metadata.sqlite3")
    metadata.migrate(2, auth.AUTH_SCHEMA)
    metadata.migrate(3, control_state.CONTROL_SCHEMA)
    metadata.migrate(4, load_dashboard_module("jobs").JOB_SCHEMA)
    metadata.migrate(5, secrets_store.VAULT_SCHEMA)
    metadata.migrate(6, persistence.DATABASE_SCHEMA)
    metadata.migrate(7, secrets_store.VAULT_METADATA_SCHEMA)
    gate = control_state.ActivityGate(metadata)
    runner = RunnerStub()
    coordinator = retrieval_operations.RetrievalOperationCoordinator(
        SettingsStub(), runner, gate, metadata
    )
    return coordinator, runner, gate


def test_start_persists_intent_and_actor_hash_after_service_readiness(tmp_path):
    coordinator, runner, _gate = make_coordinator(tmp_path)

    record = coordinator.start("embed", actor_session_id="session-private")

    assert record["phase"] == "succeeded"
    assert record["outcome_category"] == "success"
    assert record["actor_id_hash"] != "session-private"
    assert coordinator.state()["services"]["embed"]["desired_state"] == "running"
    assert runner.calls == [("start", "embed")]


def test_stopping_one_worker_does_not_change_the_other_desired_state(tmp_path):
    coordinator, runner, _gate = make_coordinator(tmp_path)
    coordinator.start("rerank", actor_session_id="operator")

    result = coordinator.stop("embed", actor_session_id="operator")

    state = coordinator.state()["services"]
    assert result["phase"] == "succeeded"
    assert state["embed"]["desired_state"] == "stopped"
    assert state["rerank"]["desired_state"] == "running"
    assert runner.calls[-1] == ("stop", "embed")


def test_restart_does_not_start_a_worker_with_deliberate_stopped_state(tmp_path):
    coordinator, runner, _gate = make_coordinator(tmp_path)

    result = coordinator.restart("rerank", actor_session_id="operator")

    assert result["phase"] == "succeeded"
    assert result["outcome_category"] == "desired_stopped"
    assert runner.calls == []
    assert coordinator.state()["services"]["rerank"]["desired_state"] == "stopped"


def test_failed_start_is_sanitized_and_keeps_requested_running_intent(tmp_path):
    retrieval_compose = load_dashboard_module("retrieval_compose")
    coordinator, runner, gate = make_coordinator(tmp_path)
    runner.failure = retrieval_compose.RetrievalComposeError("private filesystem path", "model_load_failed")

    result = coordinator.start("embed", actor_session_id="operator")

    assert result["phase"] == "failed"
    assert result["outcome_category"] == "model_load_failed"
    assert "filesystem" not in str(result)
    assert coordinator.state()["services"]["embed"]["desired_state"] == "running"
    assert gate.current() is None


def test_shared_activity_gate_rejects_retrieval_operation_while_busy(tmp_path):
    coordinator, runner, gate = make_coordinator(tmp_path)
    assert gate.acquire("vllm:start", "narrator-operation", now=100, lease_seconds=60)

    record = coordinator.start("embed", actor_session_id="operator")

    assert record["phase"] == "rejected"
    assert record["outcome_category"] == "activity_busy"
    assert runner.calls == []
    gate.release("narrator-operation")


def test_startup_reconciles_interrupted_action_by_observing_only(tmp_path):
    coordinator, runner, gate = make_coordinator(tmp_path)
    coordinator.store.set_desired_state("embed", "running")
    coordinator.store.create({
        "operation_id": "interrupted-operation",
        "service": "embed",
        "action": "start",
        "actor_id_hash": "actor-hash",
    })
    coordinator.store.update("interrupted-operation", phase="starting")
    runner.calls.clear()

    module = load_dashboard_module("retrieval_operations")
    recovered = module.RetrievalOperationCoordinator(
        SettingsStub(), runner, gate, coordinator.store.metadata
    ).reconcile_after_restart()

    assert len(recovered) == 1
    assert recovered[0]["phase"] == "reconciled"
    assert recovered[0]["recovery_state"] == {
        "desired_state": "running",
        "observed_runtime_state": "running",
        "auto_started": False,
        "auto_stopped": False,
    }
    assert runner.calls == [("validate",), ("inspect", "embed")]
