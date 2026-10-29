from __future__ import annotations

from pathlib import Path

from conftest import AsgiTestClient
from fakes import FakeKeyProtector

from app.api import create_app
from app.auth import AuthService
from app.control_state import CONTROL_SCHEMA
from app.jobs import JOB_SCHEMA
from app.persistence import MetadataStore
from app.secrets_store import AESGCMCipher, SecretsStore

DATABASE_DSN = "postgresql://dash_user:private-password@db.example.invalid:5432/analytics"
REPLACEMENT_DSN = "postgresql://dash_user:rotated-password@db.example.invalid:5432/analytics"


class FakeDatabaseMonitor:
    def __init__(self, vault: SecretsStore, state="Configured"):
        self.vault = vault
        self.state = state
        self.calls = []

    def list_status(self):
        entries = [
            item
            for item in self.vault.metadata()
            if item["integration"] == "postgresql" and item["purpose"] == "database-monitor"
        ]
        return {
            "configuration_state": {"state": "Unconfigured" if not entries else self.state},
            "databases": [
                {
                    "database_id": entry["secret_id"],
                    "name": entry["name"],
                    "state": self.state if self.state != "Configured" else "Unknown",
                    "checked_at": None,
                    "latency_ms": None,
                    "failure_category": None,
                }
                for entry in entries
            ],
        }

    def check(self, database_id):
        self.calls.append(database_id)
        return {
            "database_id": database_id,
            "state": "Unavailable",
            "checked_at": "2026-10-29T12:00:00Z",
            "latency_ms": 15,
            "failure_category": "connection_error",
        }


class FixedStatusCache:
    def get_snapshot(self):
        return {
            "inference_state": "Ready",
            "availability": "available",
            "container": None,
            "configuration": None,
            "active_profile": None,
            "telemetry": {},
            "observed_at": "2026-10-29T12:00:00Z",
            "stale": False,
            "refreshing": False,
            "last_poll_error": None,
            "last_poll_error_at": None,
        }


def make_client(tmp_path: Path, *, monitor_state="Configured"):
    metadata = MetadataStore(tmp_path / "metadata.sqlite3")
    auth = AuthService(metadata)
    metadata.migrate(3, CONTROL_SCHEMA)
    metadata.migrate(4, JOB_SCHEMA)
    vault = SecretsStore(
        metadata,
        tmp_path / "vault" / "root-key.dpapi",
        FakeKeyProtector(),
        AESGCMCipher(),
    )
    monitor = FakeDatabaseMonitor(vault, monitor_state)
    app = create_app(
        metadata_path=tmp_path / "metadata.sqlite3",
        frontend_dist=tmp_path / "missing-dist",
        auth_service=auth,
        status_cache=FixedStatusCache(),
        operation_coordinator=object(),
        job_runner=object(),
        secret_store=vault,
        database_monitor=monitor,
    )
    return AsgiTestClient(app), vault, monitor


def test_database_routes_require_auth_and_csrf(tmp_path):
    client, _vault, _monitor = make_client(tmp_path)

    assert client.get("/api/control/v1/databases").status_code == 401
    assert client.post(
        "/api/control/v1/databases",
        json_body={"name": "Analytics", "dsn": DATABASE_DSN},
    ).status_code == 401
    client.setup_admin()
    assert client.get("/api/control/v1/databases").status_code == 200
    assert client.post(
        "/api/control/v1/databases",
        json_body={"name": "Analytics", "dsn": DATABASE_DSN},
    ).status_code == 403
    saved = client.post(
        "/api/control/v1/databases",
        json_body={"name": "Analytics", "dsn": DATABASE_DSN},
        headers={"X-CSRF-Token": client.csrf_token},
    )
    assert saved.status_code == 201


def test_dsn_is_write_only_and_never_returned(tmp_path):
    client, vault, monitor = make_client(tmp_path)
    client.setup_admin()
    headers = {"X-CSRF-Token": client.csrf_token}

    created = client.post(
        "/api/control/v1/databases",
        json_body={"name": "Analytics", "dsn": DATABASE_DSN},
        headers=headers,
    )
    assert created.status_code == 201
    database_id = created.json()["database_id"]
    assert DATABASE_DSN not in created.text
    assert DATABASE_DSN not in client.get("/api/control/v1/databases").text
    assert DATABASE_DSN not in repr(vault.metadata())
    assert vault.get_for_service(database_id, "database-monitor") == DATABASE_DSN

    renamed = client.request(
        "PUT",
        f"/api/control/v1/databases/{database_id}",
        json_body={"name": "Renamed analytics"},
        headers=headers,
    )
    assert renamed.status_code == 200
    assert DATABASE_DSN not in renamed.text
    assert vault.get_for_service(database_id, "database-monitor") == DATABASE_DSN

    replaced = client.request(
        "PUT",
        f"/api/control/v1/databases/{database_id}",
        json_body={"name": "Renamed analytics", "dsn": REPLACEMENT_DSN},
        headers=headers,
    )
    assert replaced.status_code == 200
    assert REPLACEMENT_DSN not in replaced.text
    assert vault.get_for_service(database_id, "database-monitor") == REPLACEMENT_DSN

    checked = client.post(
        f"/api/control/v1/databases/{database_id}/check",
        headers=headers,
    )
    assert checked.status_code == 200
    assert monitor.calls == [database_id]
    assert DATABASE_DSN not in checked.text

    revealed = client.post(
        f"/api/control/v1/databases/{database_id}/reveal",
        headers=headers,
    )
    assert revealed.status_code == 200
    assert revealed.headers["cache-control"] == "no-store"
    assert revealed.json() == {"credential": REPLACEMENT_DSN}

    removed = client.request(
        "DELETE",
        f"/api/control/v1/databases/{database_id}",
        headers=headers,
    )
    assert removed.status_code == 204
    assert client.get("/api/control/v1/databases").json()["databases"] == []
    assert vault.metadata() == []


def test_unavailable_database_does_not_hide_other_dashboard_pages(tmp_path):
    client, _vault, _monitor = make_client(tmp_path, monitor_state="Unavailable")
    client.setup_admin()

    databases = client.get("/api/control/v1/databases")
    status = client.get("/api/control/v1/status")

    assert databases.status_code == 200
    assert status.status_code == 200
    assert status.json()["inference"]["state"] == "Ready"
