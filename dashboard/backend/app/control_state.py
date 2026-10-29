from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .configuration import ConfigurationError, load_allowlisted_profile
from .persistence import MetadataStore

CONTROL_SCHEMA = [
    (
        "CREATE TABLE dashboard_control_state ("
        "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
        "desired_state TEXT NOT NULL CHECK (desired_state IN ('running', 'stopped')), "
        "selected_profile_id TEXT, active_profile_id TEXT, last_known_good_profile_id TEXT, "
        "updated_at REAL NOT NULL)"
    ),
    (
        "CREATE TABLE dashboard_operations ("
        "operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, phase TEXT NOT NULL, "
        "started_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL, "
        "requested_profile TEXT, requested_state TEXT, outcome_category TEXT, "
        "recovery_state TEXT NOT NULL DEFAULT '{}')"
    ),
    (
        "CREATE INDEX dashboard_operations_recent ON dashboard_operations "
        "(started_at DESC, operation_id)"
    ),
    (
        "CREATE TABLE activity_gate ("
        "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), operation_id TEXT, "
        "kind TEXT, lease_until REAL, status TEXT)"
    ),
    (
        "INSERT INTO activity_gate (singleton, operation_id, kind, lease_until, status) "
        "VALUES (1, NULL, NULL, NULL, NULL)"
    ),
]
_TERMINAL_PHASES = {"succeeded", "failed", "rolled_back", "rejected", "configured", "reconciled"}
_UNSET = object()


class ActivityGateError(RuntimeError):
    """A sanitized failure while coordinating local dashboard activity."""


class OperationError(RuntimeError):
    """A sanitized inference operation failure."""


class UnknownProfileError(OperationError):
    """The requested profile is not one of the validated local profiles."""


class ControlStateStore:
    """SQLite-backed desired state and durable operation journal."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata
        if self.metadata.schema_version() < 2:
            from .auth import AUTH_SCHEMA

            self.metadata.migrate(2, AUTH_SCHEMA)
        self.metadata.migrate(3, CONTROL_SCHEMA)
        with self.metadata.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO dashboard_control_state "
                "(singleton, desired_state, selected_profile_id, active_profile_id, "
                "last_known_good_profile_id, updated_at) VALUES (1, 'stopped', NULL, NULL, NULL, ?)",
                (time.time(),),
            )

    def get_control_state(self) -> dict[str, Any]:
        with self.metadata.transaction() as connection:
            row = connection.execute(
                "SELECT desired_state, selected_profile_id, active_profile_id, "
                "last_known_good_profile_id, updated_at FROM dashboard_control_state "
                "WHERE singleton = 1"
            ).fetchone()
        return {
            "desired_state": row[0],
            "selected_profile_id": row[1],
            "active_profile_id": row[2],
            "last_known_good_profile_id": row[3],
            "updated_at": row[4],
        }

    def set_control_state(self, **values: Any) -> None:
        allowed = {
            "desired_state",
            "selected_profile_id",
            "active_profile_id",
            "last_known_good_profile_id",
        }
        if not values or not set(values) <= allowed:
            raise ValueError("Invalid dashboard control-state update")
        if "desired_state" in values and values["desired_state"] not in {"running", "stopped"}:
            raise ValueError("Invalid desired inference state")
        assignments = ", ".join(f"{key} = ?" for key in values)
        params = [*values.values(), time.time()]
        with self.metadata.transaction() as connection:
            connection.execute(
                f"UPDATE dashboard_control_state SET {assignments}, updated_at = ? "
                "WHERE singleton = 1",
                params,
            )

    def adopt_runtime_profile_if_pristine(self, profile_id: str) -> bool:
        """Adopt an externally started profile only before dashboard control was used."""
        adopted = False
        with self.metadata.transaction() as connection:
            cursor = connection.execute(
                "UPDATE dashboard_control_state SET desired_state = 'running', "
                "selected_profile_id = ?, active_profile_id = ?, updated_at = ? "
                "WHERE singleton = 1 AND desired_state = 'stopped' "
                "AND selected_profile_id IS NULL AND active_profile_id IS NULL "
                "AND last_known_good_profile_id IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM dashboard_operations)",
                (profile_id, profile_id, time.time()),
            )
            adopted = cursor.rowcount == 1
        return adopted

    def create_operation(
        self,
        *,
        operation_id: str,
        kind: str,
        requested_profile: str | None,
        requested_state: str | None,
        now: float,
    ) -> dict[str, Any]:
        with self.metadata.transaction() as connection:
            connection.execute(
                "INSERT INTO dashboard_operations "
                "(operation_id, kind, phase, started_at, updated_at, requested_profile, "
                "requested_state, recovery_state) VALUES (?, ?, 'queued', ?, ?, ?, ?, '{}')",
                (operation_id, kind, now, now, requested_profile, requested_state),
            )
        return self.get_operation(operation_id)

    def update_operation(
        self,
        operation_id: str,
        *,
        phase: str | None = None,
        outcome_category: str | None | object = _UNSET,
        recovery_state: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        if phase is None and outcome_category is _UNSET and recovery_state is None:
            return self.get_operation(operation_id)
        at = time.time() if now is None else now
        values: dict[str, Any] = {"updated_at": at}
        if phase is not None:
            values["phase"] = phase
            if phase in _TERMINAL_PHASES:
                values["completed_at"] = at
        if outcome_category is not _UNSET:
            values["outcome_category"] = outcome_category
        if recovery_state is not None:
            values["recovery_state"] = json.dumps(recovery_state, sort_keys=True)
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.metadata.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE dashboard_operations SET {assignments} WHERE operation_id = ?",
                [*values.values(), operation_id],
            )
            if cursor.rowcount != 1:
                raise OperationError("Dashboard operation record is unavailable")
        return self.get_operation(operation_id)

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        with self.metadata.transaction() as connection:
            row = connection.execute(
                "SELECT operation_id, kind, phase, started_at, updated_at, completed_at, "
                "requested_profile, requested_state, outcome_category, recovery_state "
                "FROM dashboard_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise OperationError("Dashboard operation record is unavailable")
        try:
            recovery_state = json.loads(row[9])
        except (TypeError, json.JSONDecodeError):
            recovery_state = {}
        return {
            "operation_id": row[0],
            "kind": row[1],
            "phase": row[2],
            "started_at": _timestamp(row[3]),
            "updated_at": _timestamp(row[4]),
            "completed_at": _timestamp(row[5]) if row[5] is not None else None,
            "requested_profile": row[6],
            "requested_state": row[7],
            "outcome_category": row[8],
            "recovery_state": recovery_state,
        }

    def latest_operation(self) -> dict[str, Any] | None:
        with self.metadata.transaction() as connection:
            row = connection.execute(
                "SELECT operation_id FROM dashboard_operations "
                "ORDER BY started_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        return self.get_operation(row[0]) if row else None

    def incomplete_operations(self) -> list[dict[str, Any]]:
        placeholders = ", ".join("?" for _ in _TERMINAL_PHASES)
        with self.metadata.transaction() as connection:
            rows = connection.execute(
                f"SELECT operation_id FROM dashboard_operations WHERE phase NOT IN ({placeholders}) "
                "ORDER BY started_at",
                tuple(_TERMINAL_PHASES),
            ).fetchall()
        return [self.get_operation(row[0]) for row in rows]


class ActivityGate:
    """A single SQLite lease shared by inference operations and benchmark jobs."""

    def __init__(
        self,
        metadata: MetadataStore,
        expired_lease_reconciler: Callable[[dict[str, Any]], bool | None] | None = None,
    ) -> None:
        self.metadata = metadata
        self.control_state = ControlStateStore(metadata)
        self.expired_lease_reconciler = expired_lease_reconciler

    def set_expired_lease_reconciler(
        self, reconciler: Callable[[dict[str, Any]], bool | None]
    ) -> None:
        self.expired_lease_reconciler = reconciler

    def acquire(self, kind: str, operation_id: str, now: float, lease_seconds: float) -> bool:
        if not kind or not operation_id or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("Activity gate lease values are invalid")
        expired: dict[str, Any] | None = None
        with self.metadata.transaction() as connection:
            row = connection.execute(
                "SELECT operation_id, kind, lease_until, status FROM activity_gate WHERE singleton = 1"
            ).fetchone()
            if row[0] is None:
                connection.execute(
                    "UPDATE activity_gate SET operation_id = ?, kind = ?, lease_until = ?, status = 'active' "
                    "WHERE singleton = 1",
                    (operation_id, kind, now + lease_seconds),
                )
                return True
            if row[0] == operation_id and row[3] == "active":
                connection.execute(
                    "UPDATE activity_gate SET lease_until = ?, status = 'active' WHERE singleton = 1",
                    (now + lease_seconds,),
                )
                return True
            if row[0] == operation_id:
                return False
            if row[2] is None or row[2] > now:
                return False
            expired = {
                "operation_id": row[0],
                "kind": row[1],
                "lease_until": row[2],
                "status": row[3],
            }
            connection.execute(
                "UPDATE activity_gate SET operation_id = ?, kind = ?, lease_until = ?, status = 'reconciling' "
                "WHERE singleton = 1",
                (operation_id, kind, now + lease_seconds),
            )

        if expired is None:
            return False
        reconciler = self.expired_lease_reconciler
        if reconciler is None:
            self._block_if_owned(operation_id)
            return False
        try:
            reconciled = reconciler(expired)
        except Exception as exc:  # noqa: BLE001 - adapter failures must not leak local details.
            self._block_if_owned(operation_id)
            raise ActivityGateError(f"Expired activity reconciliation failed ({type(exc).__name__})") from None
        if reconciled is False:
            self._block_if_owned(operation_id)
            return False
        with self.metadata.transaction() as connection:
            row = connection.execute(
                "SELECT operation_id, status FROM activity_gate WHERE singleton = 1"
            ).fetchone()
            if row != (operation_id, "reconciling"):
                return False
            connection.execute(
                "UPDATE activity_gate SET status = 'active', lease_until = ? WHERE singleton = 1",
                (now + lease_seconds,),
            )
        return True

    def heartbeat(self, operation_id: str, now: float, lease_seconds: float) -> None:
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("Activity gate lease values are invalid")
        with self.metadata.transaction() as connection:
            cursor = connection.execute(
                "UPDATE activity_gate SET lease_until = ? WHERE singleton = 1 "
                "AND operation_id = ? AND status = 'active'",
                (now + lease_seconds, operation_id),
            )
            if cursor.rowcount != 1:
                raise ActivityGateError("Activity gate lease is no longer owned")

    def release(self, operation_id: str) -> None:
        self._release_if_owned(operation_id)

    def current(self) -> dict[str, Any] | None:
        with self.metadata.transaction() as connection:
            row = connection.execute(
                "SELECT operation_id, kind, lease_until, status FROM activity_gate WHERE singleton = 1"
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return {"operation_id": row[0], "kind": row[1], "lease_until": row[2], "status": row[3]}

    def _block_if_owned(self, operation_id: str) -> None:
        with self.metadata.transaction() as connection:
            connection.execute(
                "UPDATE activity_gate SET lease_until = NULL, status = 'reconciliation_failed' "
                "WHERE singleton = 1 AND operation_id = ?",
                (operation_id,),
            )

    def _release_if_owned(self, operation_id: str) -> None:
        with self.metadata.transaction() as connection:
            connection.execute(
                "UPDATE activity_gate SET operation_id = NULL, kind = NULL, lease_until = NULL, status = NULL "
                "WHERE singleton = 1 AND operation_id = ?",
                (operation_id,),
            )


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class _ActivationFailure(Exception):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class OperationCoordinator:
    """Serialize inference lifecycle changes and commit only verified profiles."""

    UnknownProfileError = UnknownProfileError

    def __init__(
        self,
        settings: Any,
        runner: Any,
        status_reader: Any,
        contract_checker: Any,
        gate: ActivityGate,
        clock: Callable[[], float] = time.time,
        startup_deadline: float = 600,
        poll_interval: float = 1,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if startup_deadline <= 0 or poll_interval <= 0:
            raise ValueError("Operation deadlines must be positive")
        self.settings = settings
        self.runner = runner
        self.status_reader = status_reader
        self.contract_checker = contract_checker
        self.gate = gate
        self.control_state = ControlStateStore(gate.metadata)
        self.clock = clock
        self.startup_deadline = startup_deadline
        self.poll_interval = poll_interval
        self.sleeper = sleeper
        if self.gate.expired_lease_reconciler is None:
            self.gate.set_expired_lease_reconciler(self._reconcile_expired_lease)

    def start(self, profile_id: str) -> dict[str, Any]:
        self._require_profile(profile_id)
        operation_id, rejected = self._begin("start", profile_id, "running")
        if operation_id is None:
            return rejected
        previous = self.control_state.get_control_state()
        self.control_state.set_control_state(
            desired_state="running", selected_profile_id=profile_id
        )
        try:
            try:
                self._activate_profile(profile_id, operation_id)
            except _ActivationFailure as failure:
                return self._recover_failure(operation_id, profile_id, failure, previous)
            return self._finish(operation_id, "succeeded", "success")
        finally:
            self.gate.release(operation_id)

    def stop(self) -> dict[str, Any]:
        operation_id, rejected = self._begin("stop", None, "stopped")
        if operation_id is None:
            return rejected
        self.control_state.set_control_state(desired_state="stopped")
        try:
            self.control_state.update_operation(
                operation_id, phase="stopping", now=self.clock()
            )
            self._stop_and_release()
            return self._finish(operation_id, "succeeded", "success")
        except _ActivationFailure as failure:
            return self._finish(
                operation_id,
                "failed",
                failure.category,
                {"desired_state": "stopped", "runtime_reconciled": False},
            )
        finally:
            self.gate.release(operation_id)

    def restart(self) -> dict[str, Any]:
        state = self.control_state.get_control_state()
        if state["desired_state"] == "stopped":
            operation_id, rejected = self._begin("restart", None, "stopped")
            if operation_id is None:
                return rejected
            try:
                return self._finish(operation_id, "succeeded", "desired_stopped")
            finally:
                self.gate.release(operation_id)
        profile_id = state["last_known_good_profile_id"] or state["selected_profile_id"]
        if not profile_id:
            return self._rejected_record("restart", None, "running", "profile_unavailable")
        self._require_profile(profile_id)
        operation_id, rejected = self._begin("restart", profile_id, "running")
        if operation_id is None:
            return rejected
        previous = self.control_state.get_control_state()
        self.control_state.set_control_state(
            desired_state="running", selected_profile_id=profile_id
        )
        try:
            try:
                self._activate_profile(profile_id, operation_id, force_restart=True)
            except _ActivationFailure as failure:
                return self._recover_failure(operation_id, profile_id, failure, previous)
            return self._finish(operation_id, "succeeded", "success")
        finally:
            self.gate.release(operation_id)

    def apply_profile(self, profile_id: str) -> dict[str, Any]:
        self._require_profile(profile_id)
        before = self.control_state.get_control_state()
        desired = before["desired_state"]
        operation_id, rejected = self._begin("apply_profile", profile_id, desired)
        if operation_id is None:
            return rejected
        previous = self.control_state.get_control_state()
        self.control_state.set_control_state(selected_profile_id=profile_id)
        try:
            if desired == "stopped":
                self.control_state.update_operation(
                    operation_id, phase="configuring", now=self.clock()
                )
                try:
                    runtime = self._runner_call("inspect_project", "runtime_unavailable")
                    if self._runtime_state(runtime) in self._ACTIVE_STATES:
                        raise _ActivationFailure("desired_state_mismatch")
                    self._configure_profile(profile_id)
                except _ActivationFailure as failure:
                    self.control_state.set_control_state(
                        selected_profile_id=previous["selected_profile_id"]
                    )
                    return self._finish(operation_id, "failed", failure.category)
                return self._finish(
                    operation_id,
                    "configured",
                    "configured_while_stopped",
                    {"container_started": False},
                )
            try:
                self._activate_profile(profile_id, operation_id)
            except _ActivationFailure as failure:
                return self._recover_failure(operation_id, profile_id, failure, previous)
            return self._finish(operation_id, "succeeded", "success")
        finally:
            self.gate.release(operation_id)

    def reconcile_after_restart(self) -> dict[str, Any]:
        settings_root = self._configured_root()
        if settings_root is None:
            return {"desired_state": self.control_state.get_control_state()["desired_state"],
                    "runtime_state": "unavailable", "auto_started": False}
        reconciliation_id = uuid4().hex
        lease_seconds = max(60, self.startup_deadline + float(getattr(self.runner, "timeout", 150)) + 30)
        try:
            acquired = self.gate.acquire(
                "reconciliation", reconciliation_id, self.clock(), lease_seconds
            )
        except ActivityGateError:
            acquired = False
        if not acquired:
            return {
                "desired_state": self.control_state.get_control_state()["desired_state"],
                "runtime_state": "busy",
                "auto_started": False,
            }
        try:
            runtime = self.runner.inspect_project()
            runtime_state = self._runtime_state(runtime)
            state = self.control_state.get_control_state()
            observed_profile = runtime.get("active_profile_id")
            if (
                runtime_state == "running"
                and state["desired_state"] == "stopped"
                and state["selected_profile_id"] is None
                and state["active_profile_id"] is None
                and state["last_known_good_profile_id"] is None
                and isinstance(observed_profile, str)
            ):
                try:
                    self._require_profile(observed_profile)
                except UnknownProfileError:
                    pass
                else:
                    self.control_state.adopt_runtime_profile_if_pristine(observed_profile)
                state = self.control_state.get_control_state()
            desired = state["desired_state"]
            matches = (
                runtime_state in {"absent", "exited", "dead"}
                if desired == "stopped"
                else runtime_state in self._ACTIVE_STATES
            )
            recovery = {
                "runtime_state": runtime_state,
                "state_matches_desired": matches,
                "auto_started": False,
            }
            for record in self.control_state.incomplete_operations():
                self.control_state.update_operation(
                    record["operation_id"],
                    phase="reconciled",
                    outcome_category="interrupted",
                    recovery_state=recovery,
                    now=self.clock(),
                )
            return {
                "desired_state": desired,
                "runtime_state": runtime_state,
                "state_matches_desired": matches,
                "auto_started": False,
            }
        except _ActivationFailure:
            return {
                "desired_state": self.control_state.get_control_state()["desired_state"],
                "runtime_state": "unavailable",
                "auto_started": False,
            }
        except Exception:  # noqa: BLE001 - reconciliation results never contain adapter output.
            return {
                "desired_state": self.control_state.get_control_state()["desired_state"],
                "runtime_state": "unavailable",
                "auto_started": False,
            }
        finally:
            self.gate.release(reconciliation_id)

    _ACTIVE_STATES = frozenset({"running", "restarting", "paused", "created"})

    def _require_profile(self, profile_id: str) -> None:
        try:
            load_allowlisted_profile(profile_id)
        except ConfigurationError:
            raise UnknownProfileError("Requested dashboard profile is not available") from None

    def _configured_root(self):
        try:
            root = self.settings.load_compose_root()
            runner_root = getattr(self.runner, "project_root", None)
            if root is None:
                return None
            if runner_root is not None and root.resolve() != runner_root.resolve():
                return None
            return root
        except (OSError, RuntimeError, ValueError, TypeError):
            return None

    def _begin(
        self, kind: str, profile_id: str | None, requested_state: str | None
    ) -> tuple[str | None, dict[str, Any]]:
        now = self.clock()
        operation_id = uuid4().hex
        self.control_state.create_operation(
            operation_id=operation_id,
            kind=kind,
            requested_profile=profile_id,
            requested_state=requested_state,
            now=now,
        )
        if self._configured_root() is None:
            return None, self.control_state.update_operation(
                operation_id,
                phase="rejected",
                outcome_category="compose_root_unavailable",
                now=self.clock(),
            )
        runner_timeout = getattr(self.runner, "timeout", 150)
        lease_seconds = max(60, self.startup_deadline + float(runner_timeout) + 30)
        try:
            acquired = self.gate.acquire(kind, operation_id, now, lease_seconds)
        except ActivityGateError:
            return None, self.control_state.update_operation(
                operation_id,
                phase="rejected",
                outcome_category="reconciliation_failed",
                now=self.clock(),
            )
        if not acquired:
            return None, self.control_state.update_operation(
                operation_id,
                phase="rejected",
                outcome_category="conflict",
                now=self.clock(),
            )
        current_operation = self.control_state.get_operation(operation_id)
        if current_operation["phase"] != "queued":
            self.gate.release(operation_id)
            return None, current_operation
        self.control_state.update_operation(operation_id, phase="accepted", now=self.clock())
        return operation_id, self.control_state.get_operation(operation_id)

    def _finish(
        self,
        operation_id: str,
        phase: str,
        outcome: str,
        recovery: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.control_state.update_operation(
            operation_id,
            phase=phase,
            outcome_category=outcome,
            recovery_state=recovery or {},
            now=self.clock(),
        )

    def _rejected_record(
        self, kind: str, profile_id: str | None, requested_state: str, outcome: str
    ) -> dict[str, Any]:
        now = self.clock()
        operation_id = uuid4().hex
        self.control_state.create_operation(
            operation_id=operation_id,
            kind=kind,
            requested_profile=profile_id,
            requested_state=requested_state,
            now=now,
        )
        return self._finish(operation_id, "rejected", outcome)

    def _activate_profile(
        self, profile_id: str, operation_id: str, *, force_restart: bool = False
    ) -> None:
        runtime = self._runner_call("inspect_project", "preflight_failed")
        runtime_state = self._runtime_state(runtime)
        current_profile = runtime.get("active_profile_id") or self.control_state.get_control_state().get(
            "active_profile_id"
        )
        same_active_profile = (
            runtime_state in self._ACTIVE_STATES
            and current_profile == profile_id
            and not force_restart
        )
        self._configure_profile(profile_id)
        if same_active_profile:
            self._mark_loading(operation_id)
        else:
            if runtime_state in self._ACTIVE_STATES:
                self._stop_and_release(operation_id)
            self._assert_alias_released()
            self.control_state.update_operation(operation_id, phase="starting", now=self.clock())
            self._runner_call("start_vllm", "start_failed")
            self._invalidate_status()
            self._mark_loading(operation_id)

        if not self._wait_until_ready(operation_id):
            raise _ActivationFailure("health_timeout")
        self.control_state.update_operation(operation_id, phase="contract_check", now=self.clock())
        if not self._contract_passes(profile_id, operation_id):
            raise _ActivationFailure("contract_failed")
        self.control_state.set_control_state(
            desired_state="running",
            selected_profile_id=profile_id,
            active_profile_id=profile_id,
            last_known_good_profile_id=profile_id,
        )

    def _configure_profile(self, profile_id: str) -> None:
        self._runner_call("configure_profile", "profile_configuration_failed", profile_id)

    def _runner_call(self, method: str, category: str, *args: Any) -> Any:
        try:
            return getattr(self.runner, method)(*args)
        except Exception:  # noqa: BLE001 - never persist or expose command output.
            raise _ActivationFailure(category) from None

    def _stop_and_release(self, operation_id: str | None = None) -> None:
        if operation_id:
            self.control_state.update_operation(operation_id, phase="stopping", now=self.clock())
        runtime = self._runner_call("inspect_project", "runtime_unavailable")
        state = self._runtime_state(runtime)
        if state in self._ACTIVE_STATES:
            self._runner_call("stop_vllm", "stop_failed")
            runtime = self._runner_call("inspect_project", "runtime_unavailable")
            if self._runtime_state(runtime) in self._ACTIVE_STATES:
                raise _ActivationFailure("stop_not_confirmed")
        self._assert_alias_released()

    def _assert_alias_released(self) -> None:
        if self._runner_call("assert_alias_released", "network_unavailable") is not True:
            raise _ActivationFailure("service_alias_in_use")

    def _mark_loading(self, operation_id: str) -> None:
        self._invalidate_status()
        self.control_state.update_operation(operation_id, phase="loading", now=self.clock())

    def _invalidate_status(self) -> None:
        invalidate = getattr(self.status_reader, "invalidate", None)
        if callable(invalidate):
            invalidate()

    def _wait_until_ready(self, operation_id: str) -> bool:
        deadline = self.clock() + self.startup_deadline
        while self.clock() <= deadline:
            try:
                self.gate.heartbeat(
                    operation_id,
                    self.clock(),
                    max(60, self.startup_deadline + float(getattr(self.runner, "timeout", 150)) + 30),
                )
            except ActivityGateError:
                return False
            try:
                if callable(getattr(self.status_reader, "get_snapshot", None)):
                    snapshot = self.status_reader.get_snapshot()
                else:
                    snapshot = self.status_reader.read()
            except Exception:  # noqa: BLE001 - statuses only contribute safe categories.
                snapshot = {}
            if isinstance(snapshot, dict):
                state = snapshot.get("inference_state")
                availability = snapshot.get("availability")
                fresh = not snapshot.get("stale", False)
                if state == "Ready" and availability == "available" and fresh:
                    return True
            remaining = deadline - self.clock()
            if remaining <= 0:
                break
            self.sleeper(min(self.poll_interval, remaining))
        return False

    def _contract_passes(self, profile_id: str, operation_id: str) -> bool:
        try:
            checker = self.contract_checker
            result = checker.check(profile_id) if callable(getattr(checker, "check", None)) else checker(profile_id)
        except Exception:  # noqa: BLE001 - checker details can contain local data.
            return False
        self.gate.heartbeat(
            operation_id,
            self.clock(),
            max(60, self.startup_deadline + float(getattr(self.runner, "timeout", 150)) + 30),
        )
        if result is True:
            return True
        return isinstance(result, dict) and result.get("success") is True

    @staticmethod
    def _runtime_state(runtime: Any) -> str:
        if not isinstance(runtime, dict):
            raise _ActivationFailure("runtime_unavailable")
        state = runtime.get("runtime_state")
        if state not in {"absent", "created", "restarting", "running", "removing", "paused", "exited", "dead"}:
            raise _ActivationFailure("runtime_unavailable")
        return state

    def _recover_failure(
        self,
        operation_id: str,
        candidate_profile: str,
        failure: _ActivationFailure,
        previous_state: dict[str, Any],
    ) -> dict[str, Any]:
        if failure.category in {"preflight_failed", "profile_configuration_failed"}:
            self.control_state.set_control_state(
                desired_state=previous_state["desired_state"],
                selected_profile_id=previous_state["selected_profile_id"],
                active_profile_id=previous_state["active_profile_id"],
                last_known_good_profile_id=previous_state["last_known_good_profile_id"],
            )
            recovery = {
                "candidate": {
                    "profile_id": candidate_profile,
                    "outcome": "failed",
                    "category": failure.category,
                },
                "rollback": {"outcome": "not_needed"},
            }
            return self._finish(operation_id, "failed", failure.category, recovery)
        previous_profile = previous_state.get("last_known_good_profile_id")
        recovery: dict[str, Any] = {
            "candidate": {"profile_id": candidate_profile, "outcome": "failed", "category": failure.category},
            "rollback": {"profile_id": previous_profile, "outcome": "not_attempted"},
        }
        self.control_state.update_operation(
            operation_id, phase="rolling_back", recovery_state=recovery, now=self.clock()
        )
        if previous_profile:
            try:
                self._stop_and_release(operation_id)
                self._configure_profile(previous_profile)
                self._assert_alias_released()
                self.control_state.update_operation(operation_id, phase="starting_rollback", now=self.clock())
                self._runner_call("start_vllm", "rollback_start_failed")
                self._invalidate_status()
                self._mark_loading(operation_id)
                if not self._wait_until_ready(operation_id):
                    raise _ActivationFailure("rollback_health_failed")
                self.control_state.update_operation(operation_id, phase="rollback_contract_check", now=self.clock())
                if not self._contract_passes(previous_profile, operation_id):
                    raise _ActivationFailure("rollback_contract_failed")
                self.control_state.set_control_state(
                    desired_state="running",
                    selected_profile_id=previous_profile,
                    active_profile_id=previous_profile,
                    last_known_good_profile_id=previous_profile,
                )
                recovery["rollback"] = {"profile_id": previous_profile, "outcome": "succeeded"}
                return self._finish(
                    operation_id,
                    "rolled_back",
                    "candidate_failed_rolled_back",
                    recovery,
                )
            except _ActivationFailure as rollback_failure:
                recovery["rollback"] = {
                    "profile_id": previous_profile,
                    "outcome": "failed",
                    "category": rollback_failure.category,
                }
        try:
            self._stop_and_release(operation_id)
        except _ActivationFailure as stop_failure:
            recovery["service"] = {"outcome": "uncertain", "category": stop_failure.category}
        self.control_state.set_control_state(
            desired_state="stopped",
            selected_profile_id=previous_profile or candidate_profile,
            active_profile_id=None,
        )
        outcome = "rollback_failed" if previous_profile else "candidate_failed"
        return self._finish(operation_id, "failed", outcome, recovery)

    def _reconcile_expired_lease(self, lease: dict[str, Any]) -> bool:
        if self._configured_root() is None:
            return False
        try:
            runtime = self.runner.inspect_project()
            runtime_state = self._runtime_state(runtime)
            try:
                self.control_state.update_operation(
                    lease["operation_id"],
                    phase="reconciled",
                    outcome_category="interrupted",
                    recovery_state={
                        "runtime_state": runtime_state,
                        "state_matches_desired": (
                            runtime_state in {"absent", "exited", "dead"}
                            if self.control_state.get_control_state()["desired_state"] == "stopped"
                            else runtime_state in self._ACTIVE_STATES
                        ),
                        "auto_started": False,
                    },
                    now=self.clock(),
                )
            except OperationError:
                pass
            return True
        except Exception:  # noqa: BLE001 - a failed reconciliation must retain the lock boundary.
            return False
