from __future__ import annotations

from pathlib import Path

import pytest
from conftest import load_dashboard_module

from app.persistence import MetadataStore


class FakeClock:
    def __init__(self, now: float = 1000) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeSettings:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load_compose_root(self) -> Path:
        return self.root


class FakeRunner:
    def __init__(self, *, runtime_state: str = "absent", profile: str | None = None) -> None:
        self.runtime_state = runtime_state
        self.profile = profile
        self.alias_free = runtime_state != "running"
        self.calls: list[str] = []
        self.on_stop = None
        self.fail_configure = False

    def inspect_project(self) -> dict[str, str]:
        self.calls.append("inspect")
        return {
            "project_name": "local-ai-api-lite",
            "service": "vllm",
            "runtime_state": self.runtime_state,
            "active_profile_id": self.profile if self.runtime_state == "running" else None,
            "network": "ddu_inference_bus",
        }

    def configure_profile(self, profile_id: str) -> None:
        self.calls.append(f"configure:{profile_id}")
        if self.fail_configure:
            raise RuntimeError("synthetic Compose validation failure")
        self.profile = profile_id

    def assert_alias_released(self) -> bool:
        self.calls.append("assert_alias_released")
        return self.alias_free

    def stop_vllm(self) -> dict[str, str]:
        self.calls.append("stop")
        if self.on_stop:
            self.on_stop()
        self.runtime_state = "exited"
        self.alias_free = True
        return {"action": "stop"}

    def start_vllm(self) -> dict[str, str]:
        self.calls.append("start")
        if not self.alias_free:
            raise AssertionError("start requested before ddu-llm was released")
        self.runtime_state = "running"
        self.alias_free = False
        return {"action": "start"}


class FakeStatusReader:
    def __init__(self, states: list[str] | None = None) -> None:
        self.states = list(states or ["Ready"])
        self.observed: list[str] = []
        self.on_read = None

    def invalidate(self) -> None:
        return None

    def get_snapshot(self) -> dict[str, object]:
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        self.observed.append(state)
        if self.on_read:
            self.on_read(state)
        return {
            "availability": "available" if state != "Unknown" else "unavailable",
            "inference_state": state,
        }


class FakeContractChecker:
    def __init__(self, results: dict[str, list[bool]] | None = None) -> None:
        self.results = results or {}
        self.calls: list[str] = []
        self.on_check = None

    def check(self, profile_id: str) -> bool:
        self.calls.append(profile_id)
        if self.on_check:
            self.on_check(profile_id)
        outcomes = self.results.get(profile_id, [True])
        return outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]


def make_coordinator(
    tmp_path: Path,
    *,
    runtime_state: str = "absent",
    runtime_profile: str | None = None,
    states: list[str] | None = None,
    contract_results: dict[str, list[bool]] | None = None,
):
    control_state = load_dashboard_module("control_state")
    operations = load_dashboard_module("control_state")
    clock = FakeClock()
    metadata = MetadataStore(tmp_path / "dashboard.sqlite3")
    gate = control_state.ActivityGate(metadata)
    runner = FakeRunner(runtime_state=runtime_state, profile=runtime_profile)
    status_reader = FakeStatusReader(states)
    contract_checker = FakeContractChecker(contract_results)
    coordinator = operations.OperationCoordinator(
        settings=FakeSettings(tmp_path),
        runner=runner,
        status_reader=status_reader,
        contract_checker=contract_checker,
        clock=clock,
        gate=gate,
        startup_deadline=3,
        poll_interval=1,
        sleeper=clock.sleep,
    )
    return coordinator, runner, status_reader, contract_checker, clock


def _seed_running(coordinator, profile: str = "baseline-4k-2") -> None:
    coordinator.control_state.set_control_state(
        desired_state="running",
        selected_profile_id=profile,
        active_profile_id=profile,
        last_known_good_profile_id=profile,
    )


def test_single_writer_rejects_conflicting_operation(tmp_path):
    coordinator, runner, _status, _checker, clock = make_coordinator(tmp_path)
    assert coordinator.gate.acquire("benchmark", "benchmark-one", clock(), 60)

    record = coordinator.stop()

    assert record["outcome_category"] == "conflict"
    assert "stop" not in runner.calls


def test_stop_persists_desired_stopped_before_runner_call(tmp_path):
    coordinator, runner, _status, _checker, _clock = make_coordinator(
        tmp_path, runtime_state="running", runtime_profile="baseline-4k-2"
    )
    _seed_running(coordinator)
    observed: list[str] = []
    runner.on_stop = lambda: observed.append(
        coordinator.control_state.get_control_state()["desired_state"]
    )

    record = coordinator.stop()

    assert observed == ["stopped"]
    assert record["phase"] == "succeeded"
    assert coordinator.control_state.get_control_state()["desired_state"] == "stopped"


def test_controller_restart_never_autostarts_stopped_inference(tmp_path):
    coordinator, runner, _status, _checker, _clock = make_coordinator(tmp_path, runtime_state="exited")
    coordinator.control_state.set_control_state(desired_state="stopped")

    result = coordinator.reconcile_after_restart()

    assert result["desired_state"] == "stopped"
    assert "start" not in runner.calls


def test_start_reports_loading_until_health_and_smoke_pass(tmp_path):
    coordinator, runner, status, checker, _clock = make_coordinator(
        tmp_path, states=["Starting", "Ready"]
    )
    observed_phases: list[str] = []
    status.on_read = lambda _state: observed_phases.append(
        coordinator.control_state.latest_operation()["phase"]
    )

    record = coordinator.start("baseline-4k-2")

    assert observed_phases == ["loading", "loading"]
    assert status.observed == ["Starting", "Ready"]
    assert checker.calls == ["baseline-4k-2"]
    assert record["phase"] == "succeeded"
    assert coordinator.control_state.get_control_state()["active_profile_id"] == "baseline-4k-2"
    assert runner.calls.index("assert_alias_released") < runner.calls.index("start")


def test_candidate_profile_commits_only_after_contract_check(tmp_path):
    coordinator, _runner, _status, checker, _clock = make_coordinator(
        tmp_path,
        runtime_state="running",
        runtime_profile="baseline-4k-2",
    )
    _seed_running(coordinator)
    before_commit: list[str | None] = []
    checker.on_check = lambda _profile: before_commit.append(
        coordinator.control_state.get_control_state()["active_profile_id"]
    )

    record = coordinator.apply_profile("baseline-8k-4")

    assert before_commit == ["baseline-4k-2"]
    assert record["phase"] == "succeeded"
    assert coordinator.control_state.get_control_state()["active_profile_id"] == "baseline-8k-4"
    assert coordinator.control_state.get_control_state()["last_known_good_profile_id"] == "baseline-8k-4"


def test_invalid_candidate_configuration_does_not_stop_current_service(tmp_path):
    coordinator, runner, _status, _checker, _clock = make_coordinator(
        tmp_path,
        runtime_state="running",
        runtime_profile="baseline-4k-2",
    )
    _seed_running(coordinator)
    runner.fail_configure = True

    record = coordinator.apply_profile("baseline-8k-4")

    assert record["phase"] == "failed"
    assert record["outcome_category"] == "profile_configuration_failed"
    assert "stop" not in runner.calls
    assert "start" not in runner.calls
    assert coordinator.control_state.get_control_state()["active_profile_id"] == "baseline-4k-2"


def test_failure_rolls_back_last_known_good_and_verifies_it(tmp_path):
    coordinator, runner, _status, checker, _clock = make_coordinator(
        tmp_path,
        runtime_state="running",
        runtime_profile="baseline-4k-2",
        states=["Ready"],
        contract_results={"baseline-8k-4": [False], "baseline-4k-2": [True]},
    )
    _seed_running(coordinator)

    record = coordinator.apply_profile("baseline-8k-4")

    assert record["outcome_category"] == "candidate_failed_rolled_back"
    assert record["recovery_state"]["candidate"]["outcome"] == "failed"
    assert record["recovery_state"]["rollback"]["outcome"] == "succeeded"
    assert coordinator.control_state.get_control_state()["active_profile_id"] == "baseline-4k-2"
    assert checker.calls == ["baseline-8k-4", "baseline-4k-2"]
    assert runner.calls.count("start") == 2
    assert all("volume" not in call and call != "down" for call in runner.calls)


def test_incomplete_operation_is_reconciled_without_auto_start(tmp_path):
    coordinator, runner, _status, _checker, clock = make_coordinator(tmp_path, runtime_state="exited")
    coordinator.control_state.set_control_state(desired_state="running")
    coordinator.control_state.create_operation(
        operation_id="interrupted",
        kind="start",
        requested_profile="baseline-4k-2",
        requested_state="running",
        now=clock(),
    )
    coordinator.control_state.update_operation("interrupted", phase="starting", now=clock())

    result = coordinator.reconcile_after_restart()

    assert result["runtime_state"] == "exited"
    assert coordinator.control_state.get_operation("interrupted")["phase"] == "reconciled"
    assert coordinator.control_state.get_operation("interrupted")["outcome_category"] == "interrupted"
    assert "start" not in runner.calls


def test_unknown_profile_is_rejected_before_runner(tmp_path):
    coordinator, runner, _status, _checker, _clock = make_coordinator(tmp_path)

    with pytest.raises(coordinator.UnknownProfileError):
        coordinator.start("experimental-unvalidated")

    assert runner.calls == []


def test_operations_never_delete_volumes(tmp_path):
    coordinator, runner, _status, _checker, _clock = make_coordinator(tmp_path)
    coordinator.start("baseline-4k-2")
    coordinator.stop()

    assert not any(call in {"down", "rm", "volume", "volume prune"} for call in runner.calls)
