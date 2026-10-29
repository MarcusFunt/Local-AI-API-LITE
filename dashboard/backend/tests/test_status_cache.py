from __future__ import annotations

import json
import subprocess
import threading
import time

from conftest import load_dashboard_module


class FakeClock:
    def __init__(self, value: float = 0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("condition was not reached before timeout")


def test_first_request_returns_unknown_without_blocking_on_slow_docker():
    status_cache = load_dashboard_module("status_cache")
    started = threading.Event()
    release = threading.Event()

    class SlowReader:
        def read(self, deadline=None):
            started.set()
            release.wait(timeout=1)
            return {"availability": "available", "inference_state": "Stopped"}

    cache = status_cache.StatusCache(SlowReader(), clock=FakeClock(), command_budget=2)
    started_at = time.monotonic()
    try:
        snapshot = cache.get_snapshot()
        assert time.monotonic() - started_at < 0.1
        assert snapshot["inference_state"] == "Unknown"
        assert snapshot["refreshing"] is True
        assert started.wait(timeout=1)
    finally:
        release.set()


def test_refresh_runs_only_once_for_concurrent_requests():
    status_cache = load_dashboard_module("status_cache")
    started = threading.Event()
    release = threading.Event()

    class SlowReader:
        def __init__(self):
            self.calls = 0
            self.lock = threading.Lock()

        def read(self, deadline=None):
            with self.lock:
                self.calls += 1
            started.set()
            release.wait(timeout=1)
            return {"availability": "available", "inference_state": "Ready"}

    reader = SlowReader()
    cache = status_cache.StatusCache(reader, clock=FakeClock(), command_budget=2)
    try:
        threads = [threading.Thread(target=cache.get_snapshot) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1)
        assert started.wait(timeout=1)
        assert reader.calls == 1
    finally:
        release.set()


def test_stale_last_known_snapshot_returns_while_refresh_runs():
    status_cache = load_dashboard_module("status_cache")
    clock = FakeClock()
    second_started = threading.Event()
    release_second = threading.Event()

    class Reader:
        def __init__(self):
            self.calls = 0

        def read(self, deadline=None):
            self.calls += 1
            if self.calls == 1:
                return {"availability": "available", "inference_state": "Ready"}
            second_started.set()
            release_second.wait(timeout=1)
            return {"availability": "available", "inference_state": "Stopped"}

    reader = Reader()
    cache = status_cache.StatusCache(reader, clock=clock, max_age=5, command_budget=2)
    try:
        cache.get_snapshot()
        _wait_until(lambda: cache.get_snapshot()["inference_state"] == "Ready")
        clock.advance(6)
        snapshot = cache.get_snapshot()
        assert second_started.wait(timeout=1)
        assert snapshot["inference_state"] == "Ready"
        assert snapshot["stale"] is True
        assert snapshot["refreshing"] is True
    finally:
        release_second.set()


def test_slow_subprocess_obeys_per_command_and_total_deadline():
    docker_status = load_dashboard_module("docker_status")
    clock = FakeClock()
    timeouts: list[float] = []

    def run(args, *, timeout, **_kwargs):
        timeouts.append(timeout)
        if args[1] == "ps":
            clock.advance(4.8)
            return subprocess.CompletedProcess(args, 0, "abc123\n", "")
        assert args[1] == "inspect"
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps([{"Id": "abc123", "State": {"Status": "exited", "ExitCode": 0}}]),
            "",
        )

    reader = docker_status.DockerReader(
        docker_path="docker",
        run=run,
        timeout_seconds=3,
        command_budget=5,
        monotonic_clock=clock,
    )

    reader.read()

    assert len(timeouts) == 2
    assert timeouts[0] == 3
    assert abs(timeouts[1] - 0.2) < 1e-8


def test_failed_refresh_preserves_last_success_and_updates_failure():
    status_cache = load_dashboard_module("status_cache")
    clock = FakeClock()
    fail_second = threading.Event()
    second_started = threading.Event()

    class Reader:
        def __init__(self):
            self.calls = 0

        def read(self, deadline=None):
            self.calls += 1
            if self.calls == 1:
                return {"availability": "available", "inference_state": "Ready"}
            second_started.set()
            fail_second.wait(timeout=1)
            raise RuntimeError("sensitive local path must not leak")

    cache = status_cache.StatusCache(Reader(), clock=clock, max_age=5, command_budget=2)
    try:
        cache.get_snapshot()
        _wait_until(lambda: cache.get_snapshot()["inference_state"] == "Ready")
        clock.advance(6)
        cache.get_snapshot()
        assert second_started.wait(timeout=1)
        fail_second.set()
        _wait_until(lambda: not cache.get_snapshot()["refreshing"])
        snapshot = cache.get_snapshot()
        assert snapshot["inference_state"] == "Ready"
        assert snapshot["last_poll_error"] == "reader_error"
        assert "sensitive" not in str(snapshot)
    finally:
        fail_second.set()


def test_invalidate_marks_a_good_observation_stale_and_refreshes():
    status_cache = load_dashboard_module("status_cache")
    clock = FakeClock()
    reader_called = threading.Event()

    class Reader:
        def read(self, deadline=None):
            reader_called.set()
            return {"availability": "available", "inference_state": "Ready"}

    cache = status_cache.StatusCache(Reader(), clock=clock)
    try:
        cache.get_snapshot()
        _wait_until(lambda: not cache.get_snapshot()["refreshing"])
        cache.invalidate()
        snapshot = cache.get_snapshot()
        assert snapshot["stale"] is True
        assert snapshot["refreshing"] is True
        assert reader_called.wait(timeout=1)
    finally:
        _wait_until(lambda: not cache.get_snapshot()["refreshing"])


def test_invalidation_discards_an_observation_started_before_mutation():
    status_cache = load_dashboard_module("status_cache")
    started = threading.Event()
    release = threading.Event()

    class Reader:
        def __init__(self):
            self.calls = 0

        def read(self, deadline=None):
            self.calls += 1
            if self.calls == 1:
                started.set()
                release.wait(timeout=1)
                return {"availability": "available", "inference_state": "Stopped"}
            return {"availability": "available", "inference_state": "Ready"}

    reader = Reader()
    cache = status_cache.StatusCache(reader, clock=FakeClock())
    try:
        cache.get_snapshot()
        assert started.wait(timeout=1)
        cache.invalidate()
        release.set()
        _wait_until(lambda: not cache.get_snapshot()["refreshing"])
        _wait_until(lambda: cache.get_snapshot()["inference_state"] == "Ready")
        assert reader.calls == 2
    finally:
        release.set()


def test_no_data_is_unknown_not_zero():
    status_cache = load_dashboard_module("status_cache")
    cache = status_cache.StatusCache(
        reader=type("SlowReader", (), {"read": lambda self, deadline=None: time.sleep(0.2)})(),
        clock=FakeClock(),
        command_budget=2,
    )

    snapshot = cache.get_snapshot()

    assert snapshot["inference_state"] == "Unknown"
    assert snapshot["container"] is None
    assert snapshot["telemetry"]["cpu_percent"] is None
    assert snapshot["telemetry"]["ram_used_bytes"] is None
    assert snapshot["telemetry"]["gpu"]["utilization_percent"] is None
