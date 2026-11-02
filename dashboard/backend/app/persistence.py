from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.paths import RuntimePathError, ensure_private_directory


class MetadataStoreError(RuntimeError):
    """A sanitized metadata-storage failure safe to report to API callers."""


JOB_SCHEMA = [
    (
        "CREATE TABLE dashboard_jobs ("
        "job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL, "
        "created_at REAL NOT NULL, started_at REAL, completed_at REAL, "
        "options_json TEXT NOT NULL, summary_json TEXT NOT NULL DEFAULT '{}', "
        "error_category TEXT, artifact_id TEXT, exit_code INTEGER)"
    ),
    "CREATE INDEX dashboard_jobs_recent ON dashboard_jobs (created_at DESC, job_id)",
]

DATABASE_SCHEMA = [
    (
        "CREATE TABLE dashboard_database_status ("
        "database_id TEXT PRIMARY KEY REFERENCES vault_metadata(secret_id) ON DELETE CASCADE, "
        "state TEXT NOT NULL "
        "CHECK (state IN ('Available', 'Unavailable')), checked_at TEXT NOT NULL, "
        "latency_ms INTEGER NOT NULL, failure_category TEXT)"
    ),
]

EXAMPLES_SCHEMA = [
    (
        "CREATE TABLE dashboard_examples ("
        "example_id TEXT PRIMARY KEY CHECK (example_id = 'chat-voice'), "
        "state TEXT NOT NULL CHECK (state IN "
        "('stopped', 'starting', 'ready', 'stopping', 'failed', 'recovery-required')), "
        "started_at TEXT, last_error_category TEXT, idle_expires_at TEXT, "
        "compose_project TEXT NOT NULL CHECK (compose_project = 'local-ai-api-lite-examples'), "
        "compose_service TEXT NOT NULL CHECK (compose_service = 'chat-voice'), "
        "updated_at TEXT NOT NULL)"
    ),
]

_EXAMPLE_STATES = {"stopped", "starting", "ready", "stopping", "failed", "recovery-required"}
_EXAMPLE_ERRORS = {
    None,
    "docker_unavailable",
    "build_timeout",
    "build_failed",
    "start_timeout",
    "start_failed",
    "unhealthy",
    "stop_failed",
    "recovery_required",
}


class ExampleLifecycleStore:
    """Persist one fixed example's lifecycle metadata, never its request content."""

    def __init__(self, metadata: MetadataStore) -> None:
        self.metadata = metadata

    def get(self) -> dict[str, str | None]:
        with self.metadata.transaction() as connection:
            row = connection.execute(
                "SELECT state, started_at, last_error_category, idle_expires_at, "
                "compose_project, compose_service, updated_at FROM dashboard_examples "
                "WHERE example_id = 'chat-voice'"
            ).fetchone()
        if row is None:
            return {
                "state": "stopped",
                "started_at": None,
                "last_error_category": None,
                "idle_expires_at": None,
                "compose_project": "local-ai-api-lite-examples",
                "compose_service": "chat-voice",
                "updated_at": None,
            }
        return {
            "state": row[0],
            "started_at": row[1],
            "last_error_category": row[2],
            "idle_expires_at": row[3],
            "compose_project": row[4],
            "compose_service": row[5],
            "updated_at": row[6],
        }

    def save(
        self,
        *,
        state: str,
        started_at: str | None,
        last_error_category: str | None,
        idle_expires_at: str | None,
        updated_at: str,
    ) -> dict[str, str | None]:
        if state not in _EXAMPLE_STATES or last_error_category not in _EXAMPLE_ERRORS:
            raise ValueError("Example lifecycle metadata is invalid")
        if not isinstance(updated_at, str) or not updated_at:
            raise ValueError("Example lifecycle timestamp is invalid")
        with self.metadata.transaction() as connection:
            connection.execute(
                "INSERT INTO dashboard_examples "
                "(example_id, state, started_at, last_error_category, idle_expires_at, "
                "compose_project, compose_service, updated_at) "
                "VALUES ('chat-voice', ?, ?, ?, ?, 'local-ai-api-lite-examples', 'chat-voice', ?) "
                "ON CONFLICT(example_id) DO UPDATE SET state = excluded.state, "
                "started_at = excluded.started_at, last_error_category = excluded.last_error_category, "
                "idle_expires_at = excluded.idle_expires_at, updated_at = excluded.updated_at",
                (state, started_at, last_error_category, idle_expires_at, updated_at),
            )
        return self.get()


class MetadataStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.initialize()

    def initialize(self) -> None:
        try:
            ensure_private_directory(self.path.parent)
            with sqlite3.connect(self.path, timeout=5) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_version ("
                    "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
                    "version INTEGER NOT NULL)"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO schema_version (singleton, version) VALUES (1, 1)"
                )
        except (OSError, sqlite3.Error, RuntimePathError):
            raise MetadataStoreError("Dashboard metadata storage is unavailable") from None

    def migrate(self, version: int, statements: list[str]) -> None:
        if version < 2:
            raise MetadataStoreError("Dashboard metadata schema is invalid")
        try:
            with self.transaction() as connection:
                current = connection.execute(
                    "SELECT version FROM schema_version WHERE singleton = 1"
                ).fetchone()[0]
                if version <= current:
                    return
                if version != current + 1:
                    raise MetadataStoreError("Dashboard metadata schema is invalid")
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    "UPDATE schema_version SET version = ? WHERE singleton = 1",
                    (version,),
                )
        except sqlite3.Error:
            raise MetadataStoreError("Dashboard metadata storage is unavailable") from None

    def schema_version(self) -> int:
        """Return the current schema version through the same serialized transaction path."""
        try:
            with self.transaction() as connection:
                row = connection.execute(
                    "SELECT version FROM schema_version WHERE singleton = 1"
                ).fetchone()
            if row is None:
                raise MetadataStoreError("Dashboard metadata schema is invalid")
            return int(row[0])
        except sqlite3.Error:
            raise MetadataStoreError("Dashboard metadata storage is unavailable") from None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.path, timeout=5)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except sqlite3.Error:
            if connection is not None:
                connection.rollback()
            raise MetadataStoreError("Dashboard metadata storage is unavailable") from None
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()
