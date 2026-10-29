from __future__ import annotations

import copy
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

UNKNOWN_SNAPSHOT: dict[str, Any] = {
    "availability": "unavailable",
    "inference_state": "Unknown",
    "container": None,
    "configuration": None,
    "active_profile": None,
    "last_health_probe_at": None,
    "last_successful_health_probe_at": None,
    "last_failure_reason": None,
    "cache_path": None,
    "telemetry": {
        "cpu_percent": None,
        "ram_used_bytes": None,
        "ram_total_bytes": None,
        "cache_disk_free_bytes": None,
        "gpu": {
            "available": False,
            "scope": "device-wide",
            "reason": "Host telemetry unavailable",
            "utilization_percent": None,
            "memory_used_bytes": None,
            "memory_total_bytes": None,
            "memory_free_bytes": None,
            "temperature_c": None,
            "power_watts": None,
        },
    },
}


class StatusCache:
    """Serve the latest status observation immediately while one refresh runs."""

    def __init__(
        self,
        reader: Any,
        clock: Callable[[], float] = time.monotonic,
        max_age: float = 5,
        command_budget: float = 8,
    ) -> None:
        if max_age <= 0 or command_budget <= 0:
            raise ValueError("Status cache limits must be positive")
        self.reader = reader
        self.clock = clock
        self.max_age = max_age
        self.command_budget = command_budget
        self._lock = threading.Lock()
        self._data = copy.deepcopy(UNKNOWN_SNAPSHOT)
        self._observed_at: str | None = None
        self._observed_tick: float | None = None
        self._last_attempt_tick: float | None = None
        self._refreshing = False
        self._generation = 0
        self._last_poll_error: str | None = None
        self._last_poll_error_at: str | None = None

    def invalidate(self) -> None:
        """Force the next status read to request a fresh bounded observation."""
        with self._lock:
            self._generation += 1
            self._observed_tick = None
            self._last_attempt_tick = None
            self._last_poll_error = None
            self._last_poll_error_at = None

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self.clock()
            stale = self._observed_tick is None or now - self._observed_tick > self.max_age
            retry_ready = (
                self._last_attempt_tick is None or now - self._last_attempt_tick >= self.max_age
            )
            if stale and not self._refreshing and retry_ready:
                self._refreshing = True
                self._last_attempt_tick = now
                worker = threading.Thread(
                    target=self._refresh,
                    name="dashboard-status-refresh",
                    daemon=True,
                )
                try:
                    worker.start()
                except RuntimeError:
                    self._refreshing = False
                    self._last_poll_error = "worker_unavailable"
                    self._last_poll_error_at = _now()
            return {
                **copy.deepcopy(self._data),
                "observed_at": self._observed_at,
                "stale": stale,
                "refreshing": self._refreshing,
                "last_poll_error": self._last_poll_error,
                "last_poll_error_at": self._last_poll_error_at,
            }

    def _refresh(self) -> None:
        with self._lock:
            generation = self._generation
        try:
            result = self.reader.read(deadline=self.clock() + self.command_budget)
            if not isinstance(result, dict):
                raise TypeError("Status reader returned an invalid observation")
            if result.get("availability") != "available":
                category = result.get("poll_error_category")
                if category not in {"command_timeout", "docker_unavailable"}:
                    category = "docker_unavailable"
                self._record_failure(category, generation)
                return
            with self._lock:
                if generation != self._generation:
                    return
                self._data = {**copy.deepcopy(UNKNOWN_SNAPSHOT), **copy.deepcopy(result)}
                self._observed_at = _now()
                self._observed_tick = self.clock()
                self._last_poll_error = None
                self._last_poll_error_at = None
        except (subprocess.TimeoutExpired, TimeoutError):
            self._record_failure("command_timeout", generation)
        except Exception:  # noqa: BLE001 - failure details may contain local paths or command output.
            self._record_failure("reader_error", generation)
        finally:
            with self._lock:
                self._refreshing = False

    def _record_failure(self, category: str, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._last_poll_error = category
            self._last_poll_error_at = _now()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
