from __future__ import annotations

import math
import queue
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .persistence import DATABASE_SCHEMA, MetadataStore, MetadataStoreError
from .secrets_store import SecretsStore, VaultAccessError, VaultLockedError

DATABASE_MONITOR_PURPOSE = "database-monitor"
DATABASE_MONITOR_INTEGRATION = "postgresql"
_DATABASE_ID = re.compile(r"^[0-9a-f]{32}$")
_CONNECTOR_FAILURES = {"connection_error", "query_error", "secret_unavailable", "vault_locked", "probe_timeout", "probe_busy"}


class DatabaseMonitor:
    """Run one isolated, read-only SELECT 1 probe using a vault-held credential."""

    def __init__(
        self,
        secret_store: SecretsStore,
        connector_factory: Callable[..., Any] | None = None,
        connect_timeout: float = 3,
        query_timeout: float = 3,
        worker_gate: Any | None = None,
        metadata_store: MetadataStore | None = None,
        timeout_grace: float = 0.25,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.secret_store = secret_store
        self.connector_factory = connector_factory or _connect_postgres
        self.connect_timeout = max(0.01, float(connect_timeout))
        self.query_timeout = max(0.01, float(query_timeout))
        self.worker_gate = worker_gate or threading.BoundedSemaphore(1)
        self.metadata_store = metadata_store
        self.timeout_grace = max(0, float(timeout_grace))
        self.monotonic_clock = monotonic_clock
        if metadata_store is not None:
            metadata_store.migrate(5, DATABASE_SCHEMA)

    def configuration_state(self) -> dict[str, str]:
        try:
            configured = bool(self._database_configs())
            if not configured:
                return {"state": "Unconfigured"}
            if self.secret_store.lock_state().get("state") != "unlocked":
                return {"state": "Unavailable"}
            return {"state": "Configured"}
        except (OSError, RuntimeError, TypeError, ValueError, MetadataStoreError):
            return {"state": "Unavailable"}

    def list_status(self) -> dict[str, Any]:
        state = self.configuration_state()
        try:
            configs = self._database_configs()
        except (OSError, RuntimeError, TypeError, ValueError, MetadataStoreError):
            configs = []
        saved = self._saved_statuses()
        databases: list[dict[str, Any]] = []
        for config in configs:
            database_id = config["secret_id"]
            status = saved.get(database_id)
            if state["state"] == "Unavailable":
                status = {
                    "state": "Unavailable",
                    "checked_at": None,
                    "latency_ms": None,
                    "failure_category": "vault_locked",
                }
            databases.append(
                {
                    "database_id": database_id,
                    "name": config["name"],
                    **(status or {
                        "state": "Unknown",
                        "checked_at": None,
                        "latency_ms": None,
                        "failure_category": None,
                    }),
                }
            )
        return {"configuration_state": state, "databases": databases}

    def check(self, database_id: str) -> dict[str, Any]:
        config = next(
            (item for item in self._database_configs() if item["secret_id"] == database_id),
            None,
        )
        if config is None or not _DATABASE_ID.fullmatch(database_id):
            return self._result(database_id if _DATABASE_ID.fullmatch(database_id) else "", "Unconfigured", None, "not_configured")
        if self.secret_store.lock_state().get("state") != "unlocked":
            return self._result(database_id, "Unavailable", None, "vault_locked")
        if not self.worker_gate.acquire(blocking=False):
            return self._record(self._result(database_id, "Unavailable", 0, "probe_busy"))

        outcome: queue.Queue[tuple[str, str | None]] = queue.Queue(maxsize=1)

        def probe() -> None:
            connection = None
            phase = "secret"
            category = "connection_error"
            state = "Unavailable"
            try:
                dsn = self.secret_store.get_for_service(database_id, DATABASE_MONITOR_PURPOSE)
                phase = "connect"
                connection = self.connector_factory(
                    dsn,
                    connect_timeout=self.connect_timeout,
                    query_timeout=self.query_timeout,
                    read_only=True,
                )
                phase = "query"
                cursor = connection.cursor()
                try:
                    cursor.execute("SELECT 1")
                    if cursor.fetchone() != (1,):
                        raise RuntimeError("probe result did not match")
                finally:
                    close_cursor = getattr(cursor, "close", None)
                    if callable(close_cursor):
                        close_cursor()
                state, category = "Available", ""
            except VaultLockedError:
                category = "vault_locked"
            except VaultAccessError:
                category = "secret_unavailable"
            except Exception:  # noqa: BLE001 - connector messages can contain the DSN.
                category = "query_error" if phase == "query" else "connection_error"
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:  # noqa: BLE001, S110 - close failures are discarded.
                        pass
                try:
                    outcome.put_nowait((state, category or None))
                except queue.Full:
                    pass
                self.worker_gate.release()

        started = self.monotonic_clock()
        worker = threading.Thread(target=probe, name="dashboard-postgres-probe", daemon=True)
        worker.start()
        try:
            state, category = outcome.get(timeout=self.connect_timeout + self.query_timeout + self.timeout_grace)
        except queue.Empty:
            state, category = "Unavailable", "probe_timeout"
        latency = max(0, int((self.monotonic_clock() - started) * 1000))
        return self._record(self._result(database_id, state, latency, category))

    def _database_configs(self) -> list[dict[str, str]]:
        entries = self.secret_store.metadata()
        if not isinstance(entries, list):
            return []
        return [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("secret_id"), str)
            and _DATABASE_ID.fullmatch(entry["secret_id"])
            and entry.get("integration") == DATABASE_MONITOR_INTEGRATION
            and entry.get("purpose") == DATABASE_MONITOR_PURPOSE
            and isinstance(entry.get("name"), str)
        ]

    def _saved_statuses(self) -> dict[str, dict[str, Any]]:
        if self.metadata_store is None:
            return {}
        try:
            with self.metadata_store.transaction() as connection:
                rows = connection.execute(
                    "SELECT database_id, state, checked_at, latency_ms, failure_category "
                    "FROM dashboard_database_status"
                ).fetchall()
        except MetadataStoreError:
            return {}
        return {
            row[0]: {
                "state": row[1],
                "checked_at": row[2],
                "latency_ms": row[3],
                "failure_category": row[4],
            }
            for row in rows
        }

    def _record(self, result: dict[str, Any]) -> dict[str, Any]:
        if self.metadata_store is not None and result["state"] in {"Available", "Unavailable"}:
            try:
                with self.metadata_store.transaction() as connection:
                    connection.execute(
                        "INSERT INTO dashboard_database_status "
                        "(database_id, state, checked_at, latency_ms, failure_category) "
                        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(database_id) DO UPDATE SET "
                        "state=excluded.state, checked_at=excluded.checked_at, "
                        "latency_ms=excluded.latency_ms, failure_category=excluded.failure_category",
                        (
                            result["database_id"],
                            result["state"],
                            result["checked_at"],
                            result["latency_ms"] or 0,
                            result["failure_category"],
                        ),
                    )
            except MetadataStoreError:
                pass
        return result

    @staticmethod
    def _result(
        database_id: str,
        state: str,
        latency_ms: int | None,
        failure_category: str | None,
    ) -> dict[str, Any]:
        category = failure_category if failure_category in _CONNECTOR_FAILURES | {"not_configured"} else None
        return {
            "database_id": database_id,
            "state": state,
            "checked_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "latency_ms": latency_ms,
            "failure_category": category,
        }


def _connect_postgres(
    dsn: str,
    *,
    connect_timeout: float,
    query_timeout: float,
    read_only: bool,
) -> Any:
    if not read_only:
        raise ValueError("Database probes must be read only")
    import psycopg

    statement_timeout_ms = max(1, math.ceil(query_timeout * 1000))
    options = f"-c statement_timeout={statement_timeout_ms} -c default_transaction_read_only=on"
    return psycopg.connect(
        dsn,
        connect_timeout=max(1, math.ceil(connect_timeout)),
        options=options,
        autocommit=False,
    )
