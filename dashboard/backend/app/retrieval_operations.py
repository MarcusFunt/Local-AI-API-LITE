from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .auth import AUTH_SCHEMA
from .control_state import CONTROL_SCHEMA, ActivityGate, ActivityGateError
from .jobs import JOB_SCHEMA
from .persistence import DATABASE_SCHEMA, MetadataStore
from .retrieval_compose import RetrievalComposeError
from .secrets_store import VAULT_METADATA_SCHEMA, VAULT_SCHEMA

RETRIEVAL_OPERATION_SCHEMA = [
    (
        "CREATE TABLE retrieval_worker_desired_state ("
        "service TEXT PRIMARY KEY CHECK (service IN ('embed', 'rerank')), "
        "desired_state TEXT NOT NULL CHECK (desired_state IN ('running', 'stopped')), "
        "updated_at REAL NOT NULL)"
    ),
    (
        "CREATE TABLE retrieval_worker_operations ("
        "operation_id TEXT PRIMARY KEY, service TEXT NOT NULL CHECK (service IN ('embed', 'rerank')), "
        "action TEXT NOT NULL CHECK (action IN ('start', 'stop', 'restart')), "
        "phase TEXT NOT NULL, started_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL, "
        "outcome_category TEXT, actor_id_hash TEXT, recovery_state TEXT NOT NULL DEFAULT '{}')"
    ),
    "CREATE INDEX retrieval_worker_operations_recent ON retrieval_worker_operations (started_at DESC, operation_id)",
    "INSERT INTO retrieval_worker_desired_state (service, desired_state, updated_at) VALUES ('embed', 'stopped', 0)",
    "INSERT INTO retrieval_worker_desired_state (service, desired_state, updated_at) VALUES ('rerank', 'stopped', 0)",
]

_SERVICE_NAMES = ("embed", "rerank")
_TERMINAL_PHASES = {"succeeded", "failed", "reconciled", "rejected"}
_MAX_OPERATION_RECORDS = 1000


def _timestamp(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def ensure_retrieval_operation_schema(metadata: MetadataStore) -> None:
    if metadata.schema_version() < 2:
        metadata.migrate(2, AUTH_SCHEMA)
    if metadata.schema_version() < 3:
        metadata.migrate(3, CONTROL_SCHEMA)
    if metadata.schema_version() < 4:
        metadata.migrate(4, JOB_SCHEMA)
    if metadata.schema_version() < 5:
        metadata.migrate(5, VAULT_SCHEMA)
    if metadata.schema_version() < 6:
        metadata.migrate(6, DATABASE_SCHEMA)
    if metadata.schema_version() < 7:
        metadata.migrate(7, VAULT_METADATA_SCHEMA)
    if metadata.schema_version() < 8:
        metadata.migrate(8, RETRIEVAL_OPERATION_SCHEMA)


class RetrievalOperationStore:
    def __init__(self, metadata: MetadataStore, clock: Callable[[], float] = time.time) -> None:
        self.metadata = metadata
        self.clock = clock
        ensure_retrieval_operation_schema(metadata)
        with self.metadata.transaction() as connection:
            for service in _SERVICE_NAMES:
                connection.execute(
                    "INSERT OR IGNORE INTO retrieval_worker_desired_state "
                    "(service, desired_state, updated_at) VALUES (?, 'stopped', ?)",
                    (service, self.clock()),
                )

    def desired_state(self, service: str) -> str:
        self._require_service(service)
        with self.metadata.transaction() as connection:
            row = connection.execute(
                "SELECT desired_state FROM retrieval_worker_desired_state WHERE service = ?",
                (service,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Retrieval desired-state row is unavailable")
        return row[0]

    def set_desired_state(self, service: str, desired_state: str) -> None:
        self._require_service(service)
        if desired_state not in {"running", "stopped"}:
            raise ValueError("Retrieval desired state is invalid")
        with self.metadata.transaction() as connection:
            connection.execute(
                "UPDATE retrieval_worker_desired_state SET desired_state = ?, updated_at = ? "
                "WHERE service = ?",
                (desired_state, self.clock(), service),
            )

    def create(self, operation: dict[str, Any]) -> dict[str, Any]:
        now = self.clock()
        with self.metadata.transaction() as connection:
            connection.execute(
                "INSERT INTO retrieval_worker_operations "
                "(operation_id, service, action, phase, started_at, updated_at, actor_id_hash) "
                "VALUES (?, ?, ?, 'queued', ?, ?, ?)",
                (
                    operation["operation_id"], operation["service"], operation["action"],
                    now, now, operation.get("actor_id_hash"),
                ),
            )
            count = connection.execute("SELECT COUNT(*) FROM retrieval_worker_operations").fetchone()[0]
            if count > _MAX_OPERATION_RECORDS:
                placeholders = ",".join("?" for _ in _TERMINAL_PHASES)
                connection.execute(
                    "DELETE FROM retrieval_worker_operations WHERE operation_id IN ("
                    "SELECT operation_id FROM retrieval_worker_operations "
                    f"WHERE phase IN ({placeholders}) ORDER BY started_at ASC "
                    "LIMIT ?) AND operation_id != ?",
                    (*tuple(_TERMINAL_PHASES), count - _MAX_OPERATION_RECORDS, operation["operation_id"]),
                )
        return self.get(operation["operation_id"])

    def update(
        self,
        operation_id: str,
        *,
        phase: str,
        outcome_category: str | None = None,
        recovery_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = self.clock()
        completed_at = now if phase in _TERMINAL_PHASES else None
        recovery = json.dumps(recovery_state or {}, sort_keys=True, separators=(",", ":"))
        with self.metadata.transaction() as connection:
            cursor = connection.execute(
                "UPDATE retrieval_worker_operations SET phase = ?, updated_at = ?, completed_at = ?, "
                "outcome_category = ?, recovery_state = ? WHERE operation_id = ?",
                (phase, now, completed_at, outcome_category, recovery, operation_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Retrieval operation journal record is unavailable")
        return self.get(operation_id)

    def get(self, operation_id: str) -> dict[str, Any]:
        with self.metadata.transaction() as connection:
            row = connection.execute(
                "SELECT operation_id, service, action, phase, started_at, updated_at, completed_at, "
                "outcome_category, actor_id_hash, recovery_state FROM retrieval_worker_operations "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Retrieval operation journal record is unavailable")
        try:
            recovery_state = json.loads(row[9])
        except (TypeError, json.JSONDecodeError):
            recovery_state = {}
        return {
            "operation_id": row[0],
            "service": row[1],
            "action": row[2],
            "phase": row[3],
            "started_at": _timestamp(row[4]),
            "updated_at": _timestamp(row[5]),
            "completed_at": _timestamp(row[6]),
            "outcome_category": row[7],
            "actor_id_hash": row[8],
            "recovery_state": recovery_state,
        }

    def latest(self, service: str | None = None) -> dict[str, Any] | None:
        with self.metadata.transaction() as connection:
            if service is None:
                row = connection.execute(
                    "SELECT operation_id FROM retrieval_worker_operations "
                    "ORDER BY started_at DESC, rowid DESC LIMIT 1"
                ).fetchone()
            else:
                self._require_service(service)
                row = connection.execute(
                    "SELECT operation_id FROM retrieval_worker_operations WHERE service = ? "
                    "ORDER BY started_at DESC, rowid DESC LIMIT 1",
                    (service,),
                ).fetchone()
        return self.get(row[0]) if row else None

    def incomplete(self) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in _TERMINAL_PHASES)
        with self.metadata.transaction() as connection:
            rows = connection.execute(
                "SELECT operation_id FROM retrieval_worker_operations "
                f"WHERE phase NOT IN ({placeholders}) ORDER BY started_at",
                tuple(_TERMINAL_PHASES),
            ).fetchall()
        return [self.get(row[0]) for row in rows]

    @staticmethod
    def _require_service(service: str) -> None:
        if service not in _SERVICE_NAMES:
            raise KeyError("Retrieval service is not allowlisted")


class RetrievalOperationCoordinator:
    """Control each retrieval worker through its separate fixed Compose project."""

    lease_seconds = 420

    def __init__(
        self,
        settings: Any,
        runner: Any,
        gate: ActivityGate,
        metadata: MetadataStore,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.runner = runner
        self.gate = gate
        self.clock = clock
        self.store = RetrievalOperationStore(metadata, clock)
        self._previous_reconciler = gate.expired_lease_reconciler
        self.gate.set_expired_lease_reconciler(self._dispatch_expired_lease)

    def configured(self) -> bool:
        loader = getattr(self.settings, "load_retrieval_compose_root", None)
        return callable(loader) and loader() is not None

    def state(self) -> dict[str, Any]:
        return {
            "configured": self.configured(),
            "services": {
                service: {
                    "desired_state": self.store.desired_state(service),
                    "latest_operation": self.store.latest(service),
                }
                for service in _SERVICE_NAMES
            },
            "latest_operation": self.store.latest(),
            "activity": self.gate.current(),
        }

    def start(self, service: str, *, actor_session_id: str) -> dict[str, Any]:
        return self._operate(service, "start", actor_session_id)

    def stop(self, service: str, *, actor_session_id: str) -> dict[str, Any]:
        return self._operate(service, "stop", actor_session_id)

    def restart(self, service: str, *, actor_session_id: str) -> dict[str, Any]:
        return self._operate(service, "restart", actor_session_id)

    def _operate(self, service: str, action: str, actor_session_id: str) -> dict[str, Any]:
        self.store._require_service(service)
        operation_id = uuid4().hex
        actor_hash = hashlib.sha256(actor_session_id.encode("utf-8")).hexdigest() if actor_session_id else None
        self.store.create({
            "operation_id": operation_id,
            "service": service,
            "action": action,
            "actor_id_hash": actor_hash,
        })
        if action == "restart" and self.store.desired_state(service) == "stopped":
            return self.store.update(
                operation_id,
                phase="succeeded",
                outcome_category="desired_stopped",
                recovery_state={"runtime_action": "skipped", "auto_started": False},
            )
        try:
            accepted = self.gate.acquire(
                f"retrieval.{service}.{action}",
                operation_id,
                now=self.clock(),
                lease_seconds=self.lease_seconds,
            )
        except ActivityGateError:
            return self.store.update(operation_id, phase="rejected", outcome_category="activity_reconciliation_failed")
        if not accepted:
            return self.store.update(operation_id, phase="rejected", outcome_category="activity_busy")
        try:
            self.store.update(operation_id, phase="accepted")
            if action == "start":
                self.store.set_desired_state(service, "running")
            elif action == "stop":
                self.store.set_desired_state(service, "stopped")
            def heartbeat() -> None:
                self.gate.heartbeat(
                    operation_id,
                    now=self.clock(),
                    lease_seconds=self.lease_seconds,
                )
            self.store.update(operation_id, phase={"start": "starting", "stop": "stopping", "restart": "restarting"}[action])
            if action == "start":
                observation = self.runner.start(service, heartbeat)
            elif action == "stop":
                observation = self.runner.stop(service)
            else:
                observation = self.runner.restart(service, heartbeat)
            safe_observation = {
                key: observation.get(key)
                for key in ("service", "runtime_state", "model_id", "model_revision")
                if isinstance(observation, dict) and key in observation
            }
            return self.store.update(
                operation_id,
                phase="succeeded",
                outcome_category="success",
                recovery_state={"observation": safe_observation, "auto_started": False},
            )
        except Exception as exc:  # noqa: BLE001 - error text may contain local paths or command output.
            if isinstance(exc, RetrievalComposeError):
                category = exc.category
            elif isinstance(exc, ActivityGateError):
                category = "activity_gate_unavailable"
            else:
                category = "operation_failed"
            return self.store.update(operation_id, phase="failed", outcome_category=category)
        finally:
            self.gate.release(operation_id)

    def reconcile_after_restart(self) -> list[dict[str, Any]]:
        """Observe and journal interrupted actions without changing either service."""
        if not self.configured():
            return []
        try:
            self.runner.validate_project()
        except Exception:  # noqa: BLE001 - keep startup independent of Docker availability.
            return []
        reconciled = []
        for operation in self.store.incomplete():
            try:
                observed = self.runner.inspect_service(operation["service"])
            except RetrievalComposeError:  # Docker failure leaves the operation visibly incomplete.
                continue
            state = self.store.desired_state(operation["service"])
            reconciled.append(self.store.update(
                operation["operation_id"],
                phase="reconciled",
                outcome_category="controller_restarted",
                recovery_state={
                    "desired_state": state,
                    "observed_runtime_state": observed.get("runtime_state"),
                    "auto_started": False,
                    "auto_stopped": False,
                },
            ))
            self.gate.release(operation["operation_id"])
        return reconciled

    def _dispatch_expired_lease(self, lease: dict[str, Any]) -> bool | None:
        if str(lease.get("kind", "")).startswith("retrieval."):
            return self._reconcile_expired_lease(lease)
        if self._previous_reconciler is None:
            return False
        return self._previous_reconciler(lease)

    def _reconcile_expired_lease(self, lease: dict[str, Any]) -> bool:
        operation_id = lease.get("operation_id")
        try:
            operation = self.store.get(str(operation_id))
            self.runner.validate_project()
            observed = self.runner.inspect_service(operation["service"])
            desired = self.store.desired_state(operation["service"])
            self.store.update(
                operation["operation_id"],
                phase="reconciled",
                outcome_category="interrupted",
                recovery_state={
                    "desired_state": desired,
                    "observed_runtime_state": observed.get("runtime_state"),
                    "auto_started": False,
                    "auto_stopped": False,
                },
            )
            return True
        except Exception:  # noqa: BLE001 - retain the activity gate if safe observation fails.
            return False
