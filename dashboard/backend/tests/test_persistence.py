from __future__ import annotations

import sqlite3

import pytest

from app.persistence import MetadataStore, MetadataStoreError


def test_metadata_store_creates_wal_schema(tmp_path):
    database_path = tmp_path / "dashboard.sqlite3"
    store = MetadataStore(database_path)

    with store.transaction() as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        schema_version = connection.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0]

    assert journal_mode == "wal"
    assert foreign_keys == 1
    assert schema_version == 1
    assert database_path.is_file()


def test_schema_upgrade_is_idempotent(tmp_path):
    store = MetadataStore(tmp_path / "dashboard.sqlite3")

    store.initialize()
    store.initialize()

    with store.transaction() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_version"
        ).fetchone()[0] == 1


def test_schema_migration_is_ordered_and_idempotent(tmp_path):
    store = MetadataStore(tmp_path / "dashboard.sqlite3")
    statements = ["CREATE TABLE auth_marker (value TEXT NOT NULL)"]

    store.migrate(2, statements)
    store.migrate(2, statements)

    with store.transaction() as connection:
        version = connection.execute(
            "SELECT version FROM schema_version WHERE singleton = 1"
        ).fetchone()[0]
        marker_count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'auth_marker'"
        ).fetchone()[0]
    assert version == 2
    assert marker_count == 1


def test_database_failure_is_reported_without_path_or_sql_leak(tmp_path):
    with pytest.raises(MetadataStoreError) as caught:
        MetadataStore(tmp_path)

    message = str(caught.value)
    assert message == "Dashboard metadata storage is unavailable"
    assert str(tmp_path) not in message
    assert "CREATE TABLE" not in message
    assert not isinstance(caught.value.__cause__, sqlite3.Error)
