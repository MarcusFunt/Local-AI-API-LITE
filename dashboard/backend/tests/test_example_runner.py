from __future__ import annotations

import subprocess
import threading

import pytest

from app.control_state import ActivityGate, ControlStateStore
from app.example_runner import (
    DockerComposeAdapter,
    ExampleActivityConflict,
    ExampleDependencyUnavailable,
    ExampleDockerUnavailable,
    ExampleRunner,
)
from app.examples import ExampleDependencyStatus
from app.persistence import (
    DATABASE_SCHEMA,
    EXAMPLES_SCHEMA,
    JOB_SCHEMA,
    MetadataStore,
)
from app.retrieval_operations import ensure_retrieval_operation_schema
from app.secrets_store import VAULT_METADATA_SCHEMA, VAULT_SCHEMA


class FakeDependencies:
    def __init__(self, chat="ready", voice="ready"):
        self.chat = chat
        self.voice = voice

    def read(self):
        return (
            ExampleDependencyStatus(state=self.chat, active_profile="baseline-8k-4"),
            ExampleDependencyStatus(state=self.voice),
        )


class FakeCompose:
    def __init__(self):
        self.available = True
        self.image_present = False
        self.state = "absent"
        self.health = "unavailable"
        self.inspect_error = None
        self.build_error = None
        self.start_error = None
        self.stop_confirms_exit = True
        self.commands: list[tuple] = []

    def ensure_available(self):
        self.commands.append(("available",))
        if not self.available:
            raise ExampleDockerUnavailable()

    def image_exists(self):
        self.commands.append(("image_exists",))
        return self.image_present

    def build(self, timeout):
        self.commands.append(("build", timeout))
        if self.build_error:
            raise self.build_error
        self.image_present = True

    def start(self, token, timeout):
        self.commands.append(("start", token, timeout))
        if self.start_error:
            raise self.start_error
        self.state = "running"
        self.health = "healthy"

    def inspect(self):
        self.commands.append(("inspect",))
        if not self.available:
            raise ExampleDockerUnavailable()
        if self.inspect_error:
            raise self.inspect_error
        return {"state": self.state, "health": self.health}

    def stop(self, timeout):
        self.commands.append(("stop", timeout))
        if self.stop_confirms_exit:
            self.state = "exited"
            self.health = "unavailable"


def _runtime(tmp_path, *, dependencies=None, compose=None, **runner_options):
    metadata = MetadataStore(tmp_path / "metadata.sqlite3")
    ControlStateStore(metadata)
    for version, statements in (
        (4, JOB_SCHEMA),
        (5, VAULT_SCHEMA),
        (6, DATABASE_SCHEMA),
        (7, VAULT_METADATA_SCHEMA),
    ):
        if metadata.schema_version() < version:
            metadata.migrate(version, statements)
    ensure_retrieval_operation_schema(metadata)
    if metadata.schema_version() < 9:
        metadata.migrate(9, EXAMPLES_SCHEMA)
    gate = ActivityGate(metadata)
    fake_compose = compose or FakeCompose()
    background = runner_options.pop("background", False)
    runner = ExampleRunner(
        metadata=metadata,
        gate=gate,
        dependency_reader=dependencies or FakeDependencies(),
        compose=fake_compose,
        background=background,
        **runner_options,
    )
    return metadata, gate, fake_compose, runner


def test_start_builds_and_runs_only_after_required_narrator_is_ready(tmp_path):
    _, gate, compose, runner = _runtime(tmp_path)

    status = runner.start("chat-voice")

    assert status.state == "ready"
    assert compose.commands[0] == ("available",)
    assert ("build", runner.build_timeout) in compose.commands
    starts = [command for command in compose.commands if command[0] == "start"]
    assert len(starts) == 1
    assert starts[0][1]
    assert starts[0][2] == runner.start_timeout
    assert gate.current() == {
        "operation_id": runner.gate_operation_id,
        "kind": "example",
        "lease_until": None,
        "status": "active",
    }


def test_start_rejects_missing_narrator_without_running_docker(tmp_path):
    _, gate, compose, runner = _runtime(tmp_path, dependencies=FakeDependencies(chat="stopped"))

    with pytest.raises(ExampleDependencyUnavailable):
        runner.start("chat-voice")

    assert compose.commands == []
    assert gate.current() is None


@pytest.mark.parametrize("kind", ["job", "inference"])
def test_start_cannot_take_activity_gate_from_jobs_or_profile_changes(tmp_path, kind):
    _, gate, compose, runner = _runtime(tmp_path, clock=lambda: 100)
    assert gate.acquire(kind, f"{kind}-operation", now=100, lease_seconds=60)

    with pytest.raises(ExampleActivityConflict):
        runner.start("chat-voice")

    assert not any(command[0] in {"build", "start"} for command in compose.commands)
    assert gate.current()["operation_id"] == f"{kind}-operation"


def test_public_status_disables_launch_when_another_activity_owns_the_gate(tmp_path):
    _, gate, _, runner = _runtime(tmp_path, clock=lambda: 100)
    assert gate.acquire("job", "job-operation", now=100, lease_seconds=60)

    status = runner.list_status()[0]

    assert status.activity_available is False


def test_chat_starts_when_speech_services_are_unavailable(tmp_path):
    _, _, _, runner = _runtime(tmp_path, dependencies=FakeDependencies(voice="unavailable"))

    status = runner.start("chat-voice")

    assert status.state == "ready"
    assert status.chat_dependency.state == "ready"
    assert status.voice_dependency.state == "unavailable"
    assert status.missing_dependencies == ["ddu-stt", "ddu-tts"]


def test_stop_releases_gate_only_after_docker_confirms_exit(tmp_path):
    _, gate, compose, runner = _runtime(tmp_path)
    runner.start("chat-voice")
    compose.stop_confirms_exit = False

    pending = runner.stop("chat-voice")

    assert pending.state == "recovery-required"
    assert gate.current() is not None
    assert gate.current()["operation_id"] == runner.gate_operation_id

    compose.state = "exited"
    stopped = runner.reconcile()[0]

    assert stopped.state == "stopped"
    assert gate.current() is None


def test_stop_wins_a_race_with_a_container_start_in_progress(tmp_path):
    class BlockingCompose(FakeCompose):
        def __init__(self):
            super().__init__()
            self.start_entered = threading.Event()
            self.release_start = threading.Event()

        def start(self, token, timeout):
            self.commands.append(("start", token, timeout))
            self.start_entered.set()
            assert self.release_start.wait(timeout=2)
            self.state = "running"
            self.health = "healthy"

    _, gate, compose, runner = _runtime(tmp_path, compose=BlockingCompose(), background=True)

    assert runner.start("chat-voice").state == "starting"
    assert compose.start_entered.wait(timeout=1)
    stop_result = {}
    stop_thread = threading.Thread(
        target=lambda: stop_result.update(status=runner.stop("chat-voice"))
    )
    stop_thread.start()
    assert runner._stop_requested.wait(timeout=1)
    compose.release_start.set()
    runner._start_thread.join(timeout=2)
    stop_thread.join(timeout=2)

    assert stop_result["status"].state == "stopped"
    assert compose.state == "exited"
    assert runner.list_status()[0].state == "stopped"
    assert gate.current() is None


def test_stop_during_image_build_holds_gate_until_start_worker_finishes(tmp_path):
    class BlockingBuildCompose(FakeCompose):
        def __init__(self):
            super().__init__()
            self.build_entered = threading.Event()
            self.release_build = threading.Event()

        def build(self, timeout):
            self.commands.append(("build", timeout))
            self.build_entered.set()
            assert self.release_build.wait(timeout=2)
            self.image_present = True

    _, gate, compose, runner = _runtime(tmp_path, compose=BlockingBuildCompose(), background=True)

    assert runner.start("chat-voice").state == "starting"
    assert compose.build_entered.wait(timeout=1)
    stop_result = {}
    stop_thread = threading.Thread(
        target=lambda: stop_result.update(status=runner.stop("chat-voice"))
    )
    stop_thread.start()
    stop_thread.join(timeout=1)

    assert not stop_thread.is_alive()
    assert stop_result["status"].state == "stopping"
    assert gate.current()["operation_id"] == runner.gate_operation_id
    with pytest.raises(ExampleActivityConflict):
        runner.start("chat-voice")

    compose.release_build.set()
    runner._start_thread.join(timeout=2)

    assert not runner._start_thread.is_alive()
    assert not any(command[0] == "start" for command in compose.commands)
    assert runner.list_status()[0].state == "stopped"
    assert gate.current() is None


def test_idle_timeout_stops_container_and_releases_gate(tmp_path):
    now = [10_000.0]
    _, gate, compose, runner = _runtime(tmp_path, clock=lambda: now[0])
    runner.start("chat-voice")
    before = runner.list_status()[0].idle_expires_at
    now[0] += 1801

    status = runner.check_idle_timeout()

    assert status.state == "stopped"
    assert status.idle_expires_at is None
    assert before is not None
    assert compose.state == "exited"
    assert gate.current() is None


def test_proxy_activity_extends_only_the_lifecycle_idle_deadline(tmp_path):
    now = [10_000.0]
    metadata, _, _, runner = _runtime(tmp_path, clock=lambda: now[0])
    runner.start("chat-voice")
    before = runner.list_status()[0].idle_expires_at
    now[0] += 300

    status = runner.touch_activity()

    assert status.state == "ready"
    assert status.idle_expires_at is not None
    assert before is not None
    assert status.idle_expires_at > before
    with metadata.transaction() as connection:
        row = connection.execute("SELECT * FROM dashboard_examples").fetchone()
    assert row[1] == "ready"
    assert len(row) == 8


def test_upstream_token_is_memory_only_rotates_each_launch_and_clears_on_stop(tmp_path):
    tokens = iter(["first-run-token", "second-run-token"])
    metadata, _, _, runner = _runtime(tmp_path, token_factory=lambda: next(tokens))

    runner.start("chat-voice")
    assert runner.get_upstream_token() == "first-run-token"
    with metadata.transaction() as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(dashboard_examples)")
        }
        values = connection.execute("SELECT * FROM dashboard_examples").fetchone()
    assert "token" not in " ".join(columns)
    assert "first-run-token" not in repr(values)

    runner.stop("chat-voice")
    with pytest.raises(ExampleActivityConflict):
        runner.get_upstream_token()
    runner.start("chat-voice")
    assert runner.get_upstream_token() == "second-run-token"


def test_docker_unavailable_keeps_unknown_runtime_gated(tmp_path):
    _, gate, compose, runner = _runtime(tmp_path)
    compose.available = False

    with pytest.raises(ExampleDockerUnavailable):
        runner.start("chat-voice")

    assert gate.current()["operation_id"] == runner.gate_operation_id
    assert runner.list_status()[0].state == "recovery-required"
    assert runner.list_status()[0].last_error_category == "docker_unavailable"


def test_reconciliation_releases_gate_after_docker_returns_and_container_is_absent(tmp_path):
    _, gate, compose, runner = _runtime(tmp_path)
    compose.available = False

    with pytest.raises(ExampleDockerUnavailable):
        runner.start("chat-voice")

    compose.available = True
    status = runner.list_status()[0]

    assert status.state == "stopped"
    assert gate.current() is None


def test_start_timeout_with_live_container_keeps_gate_reserved(tmp_path):
    _, gate, compose, runner = _runtime(
        tmp_path,
        start_timeout=2,
        poll_interval=1,
        monotonic_clock=iter([0, 0, 1, 2, 3, 4]).__next__,
        sleeper=lambda _seconds: None,
    )
    compose.start = lambda _token, _timeout: setattr(compose, "state", "running")
    compose.health = "starting"

    status = runner.start("chat-voice")

    assert status.state == "recovery-required"
    assert status.last_error_category == "start_timeout"
    assert gate.current()["operation_id"] == runner.gate_operation_id


def test_unhealthy_container_is_stopped_and_gate_released_after_exit(tmp_path):
    _, gate, compose, runner = _runtime(tmp_path)

    def start_unhealthy(_token, _timeout):
        compose.state = "running"
        compose.health = "unhealthy"

    compose.start = start_unhealthy

    status = runner.start("chat-voice")

    assert status.state == "failed"
    assert status.last_error_category == "unhealthy"
    assert ("stop", runner.stop_timeout) in compose.commands
    assert gate.current() is None


def test_restart_reconciliation_never_starts_container_and_requires_recovery(tmp_path):
    metadata, gate, compose, first = _runtime(tmp_path)
    first.start("chat-voice")
    before = len([command for command in compose.commands if command[0] == "start"])

    restarted = ExampleRunner(
        metadata=metadata,
        gate=gate,
        dependency_reader=FakeDependencies(),
        compose=compose,
        background=False,
    )
    compose.state = "running"
    compose.health = "healthy"

    status = restarted.reconcile()[0]

    after = len([command for command in compose.commands if command[0] == "start"])
    assert status.state == "recovery-required"
    assert before == after
    assert gate.current()["operation_id"] == restarted.gate_operation_id
    with pytest.raises(ExampleActivityConflict):
        restarted.start("chat-voice")


def test_restart_reconciliation_releases_gate_after_confirmed_exit(tmp_path):
    metadata, gate, compose, first = _runtime(tmp_path)
    first.start("chat-voice")
    compose.state = "exited"

    restarted = ExampleRunner(
        metadata=metadata,
        gate=gate,
        dependency_reader=FakeDependencies(),
        compose=compose,
        background=False,
    )
    status = restarted.reconcile()[0]

    assert status.state == "stopped"
    assert gate.current() is None


def test_uncertain_container_state_keeps_gate_reserved(tmp_path):
    metadata, gate, compose, first = _runtime(tmp_path)
    first.start("chat-voice")
    restarted = ExampleRunner(
        metadata=metadata,
        gate=gate,
        dependency_reader=FakeDependencies(),
        compose=compose,
        background=False,
    )
    compose.inspect_error = ExampleDockerUnavailable()

    status = restarted.reconcile()[0]

    assert status.state == "recovery-required"
    assert status.last_error_category == "docker_unavailable"
    assert gate.current()["operation_id"] == restarted.gate_operation_id


def test_unknown_example_id_is_rejected_before_docker_command(tmp_path):
    _, gate, compose, runner = _runtime(tmp_path)

    with pytest.raises(ValueError):
        runner.start("other")

    assert compose.commands == []
    assert gate.current() is None


def test_start_requires_recovery_for_an_existing_untracked_container(tmp_path):
    _, gate, compose, runner = _runtime(tmp_path)
    compose.state = "running"
    compose.health = "healthy"

    with pytest.raises(ExampleActivityConflict):
        runner.start("chat-voice")

    assert not any(command[0] == "start" for command in compose.commands)
    assert runner.list_status()[0].state == "recovery-required"
    assert gate.current()["operation_id"] == runner.gate_operation_id


def test_lifecycle_metadata_is_bounded_to_fixed_identity_and_content_free(tmp_path):
    metadata, _, _, runner = _runtime(tmp_path)
    runner.start("chat-voice")

    with metadata.transaction() as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(dashboard_examples)")
        }
        row = connection.execute("SELECT * FROM dashboard_examples").fetchone()

    assert columns == {
        "example_id",
        "state",
        "started_at",
        "last_error_category",
        "idle_expires_at",
        "compose_project",
        "compose_service",
        "updated_at",
    }
    assert row[0] == "chat-voice"
    assert row[5] == "local-ai-api-lite-examples"
    assert row[6] == "chat-voice"


def test_docker_adapter_uses_fixed_argument_arrays_and_passes_only_its_run_token():
    from app.examples import load_example_registry

    commands = []

    def fake_run(arguments, **kwargs):
        commands.append((arguments, kwargs))
        stdout = "" if "image" in arguments else "Docker version"
        return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")

    example = load_example_registry()["chat-voice"]
    adapter = DockerComposeAdapter(example, docker_path="docker", run=fake_run)
    adapter.ensure_available()
    assert adapter.image_exists() is False
    adapter.build(timeout=600)
    adapter.start("per-run-secret", timeout=90)
    adapter.stop(timeout=20)

    compose_calls = [call for call in commands if "compose" in call[0]]
    assert compose_calls[0][0] == [
        "docker",
        "compose",
        "--file",
        str(example.compose_file),
        "--project-name",
        "local-ai-api-lite-examples",
        "build",
        "--pull",
        "chat-voice",
    ]
    assert compose_calls[1][0][-7:] == [
        "up",
        "--detach",
        "--no-deps",
        "--no-build",
        "--pull",
        "never",
        "chat-voice",
    ]
    assert compose_calls[1][1]["env"]["EXAMPLE_UPSTREAM_TOKEN"] == "per-run-secret"
    assert "EXAMPLE_UPSTREAM_TOKEN" not in compose_calls[0][1]["env"]
    assert all(isinstance(call[0], list) for call in commands)
    assert all("shell" not in call[1] for call in commands)
