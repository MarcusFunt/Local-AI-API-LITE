from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.paths import RuntimePathError, ensure_private_directory


class MetadataStoreError(RuntimeError):
    """A sanitized metadata-storage failure safe to report to API callers."""


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
