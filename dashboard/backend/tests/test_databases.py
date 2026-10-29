from __future__ import annotations

import threading
import time

import pytest
from conftest import load_dashboard_module

from app.auth import AUTH_SCHEMA
from app.control_state import CONTROL_SCHEMA
from app.jobs import JOB_SCHEMA
from app.persistence import MetadataStore

DATABASE_ID = "a" * 32


def _metadata_store(path):
    store = MetadataStore(path / "metadata.sqlite3")
    store.migrate(2, AUTH_SCHEMA)
    store.migrate(3, CONTROL_SCHEMA)
    store.migrate(4, JOB_SCHEMA)
    return store


class FakeSecretStore:
    def __init__(self, *, entries=None, values=None, state="unlocked"):
        self.entries = entries or []
        self.values = values or {}
        self.state = state
        self.calls = []

    def metadata(self):
        return list(self.entries)

    def lock_state(self):
        return {"state": self.state}

    def get_for_service(self, secret_id, purpose):
        self.calls.append((secret_id, purpose))
        if secret_id not in self.values:
            raise RuntimeError("secret unavailable")
        return self.values[secret_id]


def _db_secret(secret_id=DATABASE_ID):
    return {
        "secret_id": secret_id,
        "name": "Development database",
        "integration": "postgresql",
        "purpose": "database-monitor",
        "created_at": "2026-10-29T12:00:00Z",
    }


class FakeCursor:
    def __init__(self, *, delay=0):
        self.delay = delay
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        if self.delay:
            time.sleep(self.delay)

    def fetchone(self):
        return (1,)

    def close(self):
        pass


class FakeConnection:
    def __init__(self, *, cursor=None):
        self._cursor = cursor or FakeCursor()
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def test_unconfigured_differs_from_unavailable():
    databases = load_dashboard_module("databases")

    assert databases.DatabaseMonitor(FakeSecretStore()).configuration_state()["state"] == "Unconfigured"
    locked = FakeSecretStore(entries=[_db_secret()], state="locked")
    assert databases.DatabaseMonitor(locked).configuration_state()["state"] == "Unavailable"


def test_probe_runs_only_select_one(tmp_path):
    databases = load_dashboard_module("databases")
    secret = "postgresql://user:secret@localhost/example"
    secret_store = FakeSecretStore(entries=[_db_secret()], values={DATABASE_ID: secret})
    cursor = FakeCursor()
    connection = FakeConnection(cursor=cursor)
    connector_calls = []

    def connector(dsn, **kwargs):
        connector_calls.append((dsn, kwargs))
        return connection

    monitor = databases.DatabaseMonitor(
        secret_store,
        connector_factory=connector,
        metadata_store=_metadata_store(tmp_path),
    )

    result = monitor.check(DATABASE_ID)

    assert result["state"] == "Available"
    assert cursor.statements == ["SELECT 1"]
    assert connector_calls[0][0] == secret
    assert connector_calls[0][1]["read_only"] is True
    assert secret_store.calls == [(DATABASE_ID, "database-monitor")]
    assert connection.closed


@pytest.mark.parametrize("phase", ["connect", "query"])
def test_connect_and_query_deadlines_are_enforced(tmp_path, phase):
    databases = load_dashboard_module("databases")
    release = threading.Event()
    secret_store = FakeSecretStore(entries=[_db_secret()], values={DATABASE_ID: "dsn-secret"})

    def connector(_dsn, **_kwargs):
        if phase == "connect":
            release.wait(timeout=1)
        return FakeConnection(cursor=FakeCursor(delay=0.2 if phase == "query" else 0))

    monitor = databases.DatabaseMonitor(
        secret_store,
        connector_factory=connector,
        connect_timeout=0.03,
        query_timeout=0.03,
        timeout_grace=0.01,
        metadata_store=_metadata_store(tmp_path),
    )
    started = time.monotonic()
    try:
        result = monitor.check(DATABASE_ID)
        assert time.monotonic() - started < 0.15
        assert result["state"] == "Unavailable"
        assert result["failure_category"] == "probe_timeout"
    finally:
        release.set()


def test_connection_failure_does_not_break_status_service():
    databases = load_dashboard_module("databases")
    secrets = FakeSecretStore(entries=[_db_secret()], values={DATABASE_ID: "postgresql://private-dsn"})
    monitor = databases.DatabaseMonitor(
        secrets,
        connector_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("driver details")),
    )

    status_snapshot = {"inference_state": "Ready", "availability": "available"}
    failed_database = monitor.check(DATABASE_ID)

    assert failed_database["state"] == "Unavailable"
    assert status_snapshot["inference_state"] == "Ready"


def test_dsn_never_appears_in_result_or_logs(caplog):
    databases = load_dashboard_module("databases")
    dsn = "postgresql://local_user:private_password@db.internal:5432/secret_db"
    monitor = databases.DatabaseMonitor(
        FakeSecretStore(entries=[_db_secret()], values={DATABASE_ID: dsn}),
        connector_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(dsn)),
    )

    result = monitor.check(DATABASE_ID)

    assert dsn not in str(result)
    assert dsn not in caplog.text
    assert result["failure_category"] == "connection_error"


def test_database_credentials_are_read_only_vault_values():
    databases = load_dashboard_module("databases")
    secret_store = FakeSecretStore(entries=[_db_secret()], values={DATABASE_ID: "opaque-dsn"})
    monitor = databases.DatabaseMonitor(
        secret_store,
        connector_factory=lambda _dsn, **_kwargs: FakeConnection(),
    )

    result = monitor.check(DATABASE_ID)

    assert result["state"] == "Available"
    assert secret_store.calls == [(DATABASE_ID, "database-monitor")]
