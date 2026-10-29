from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.paths import RuntimePathError, ensure_private_directory
from app.persistence import MetadataStore, MetadataStoreError

VAULT_SCHEMA = [
    (
        "CREATE TABLE vault_metadata ("
        "secret_id TEXT PRIMARY KEY, name TEXT NOT NULL, integration TEXT NOT NULL, "
        "purpose TEXT NOT NULL, created_at TEXT NOT NULL)"
    ),
    (
        "CREATE TABLE vault_values ("
        "secret_id TEXT PRIMARY KEY REFERENCES vault_metadata(secret_id) ON DELETE CASCADE, "
        "nonce BLOB NOT NULL, ciphertext BLOB NOT NULL)"
    ),
    (
        "CREATE TABLE vault_reveals ("
        "token_digest TEXT PRIMARY KEY, secret_id TEXT NOT NULL "
        "REFERENCES vault_metadata(secret_id) ON DELETE CASCADE, expires_at TEXT NOT NULL, "
        "consumed_at TEXT)"
    ),
]
REVEAL_LIFETIME = timedelta(minutes=1)
MAX_SECRET_LENGTH = 65536
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class VaultLockedError(RuntimeError):
    """The vault key or encrypted value is unavailable or cannot be verified."""

    def __init__(self) -> None:
        super().__init__("Dashboard vault is locked")


class VaultAccessError(RuntimeError):
    """A secret or one-time reveal is unavailable to this operation."""

    def __init__(self) -> None:
        super().__init__("Dashboard secret is unavailable")


class KeyProtector(Protocol):
    def protect(self, plaintext: bytes) -> bytes: ...

    def unprotect(self, protected: bytes) -> bytes: ...


class WindowsDpapiProtector:
    """Protect root-key bytes with Windows DPAPI for the current user only."""

    def protect(self, plaintext: bytes) -> bytes:
        return self._transform(plaintext, protect=True)

    def unprotect(self, protected: bytes) -> bytes:
        return self._transform(protected, protect=False)

    def _transform(self, data: bytes, *, protect: bool) -> bytes:
        if os.name != "nt":
            raise VaultLockedError()

        from ctypes import wintypes

        class DataBlob(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
            ]

        input_buffer = ctypes.create_string_buffer(data, len(data))
        input_blob = DataBlob(
            len(data),
            ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)),
        )
        output_blob = DataBlob()
        try:
            crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
            kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
            if protect:
                function = crypt32.CryptProtectData
                function.argtypes = [
                    ctypes.POINTER(DataBlob),
                    wintypes.LPCWSTR,
                    ctypes.POINTER(DataBlob),
                    wintypes.LPVOID,
                    wintypes.LPVOID,
                    wintypes.DWORD,
                    ctypes.POINTER(DataBlob),
                ]
                function.restype = wintypes.BOOL
                success = function(
                    ctypes.byref(input_blob),
                    "Local-AI-API-LITE Dashboard Vault",
                    None,
                    None,
                    None,
                    CRYPTPROTECT_UI_FORBIDDEN,
                    ctypes.byref(output_blob),
                )
            else:
                function = crypt32.CryptUnprotectData
                function.argtypes = [
                    ctypes.POINTER(DataBlob),
                    ctypes.POINTER(wintypes.LPWSTR),
                    ctypes.POINTER(DataBlob),
                    wintypes.LPVOID,
                    wintypes.LPVOID,
                    wintypes.DWORD,
                    ctypes.POINTER(DataBlob),
                ]
                function.restype = wintypes.BOOL
                success = function(
                    ctypes.byref(input_blob),
                    None,
                    None,
                    None,
                    None,
                    CRYPTPROTECT_UI_FORBIDDEN,
                    ctypes.byref(output_blob),
                )
            if not success or not output_blob.pbData:
                raise VaultLockedError()
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        except (AttributeError, OSError, ctypes.ArgumentError):
            raise VaultLockedError() from None
        finally:
            if output_blob.pbData:
                ctypes.memset(output_blob.pbData, 0, output_blob.cbData)
                kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
                kernel32.LocalFree.restype = wintypes.HLOCAL
                kernel32.LocalFree(output_blob.pbData)


class AESGCMCipher:
    def encrypt(self, key: bytes, nonce: bytes, plaintext: bytes, associated_data: bytes) -> bytes:
        return AESGCM(key).encrypt(nonce, plaintext, associated_data)

    def decrypt(self, key: bytes, nonce: bytes, ciphertext: bytes, associated_data: bytes) -> bytes:
        return AESGCM(key).decrypt(nonce, ciphertext, associated_data)


class SecretsStore:
    def __init__(
        self,
        store: MetadataStore,
        key_file: Path,
        protector: KeyProtector,
        cipher: AESGCMCipher,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[int], str] | None = None,
    ) -> None:
        self.store = store
        self.key_file = Path(key_file)
        self.protector = protector
        self.cipher = cipher
        self.clock = clock or (lambda: datetime.now(UTC))
        self.token_factory = token_factory or (lambda size: secrets.token_urlsafe(size))
        try:
            ensure_private_directory(self.key_file.parent)
            self.store.migrate(5, VAULT_SCHEMA)
        except (RuntimePathError, MetadataStoreError):
            raise VaultLockedError() from None

    def put(self, name: str, integration: str, purpose: str, value: str) -> str:
        _validate_metadata(name, integration, purpose)
        if not isinstance(value, str) or not value or len(value) > MAX_SECRET_LENGTH:
            raise VaultAccessError()
        secret_id = uuid4().hex
        now = _format_time(_as_utc(self.clock()))
        nonce = secrets.token_bytes(12)
        try:
            with self.store.transaction() as connection:
                key = self._root_key_for_write(connection)
                associated_data = _associated_data(secret_id, name, integration, purpose)
                ciphertext = self.cipher.encrypt(
                    key,
                    nonce,
                    value.encode("utf-8"),
                    associated_data,
                )
                connection.execute(
                    "INSERT INTO vault_metadata "
                    "(secret_id, name, integration, purpose, created_at) VALUES (?, ?, ?, ?, ?)",
                    (secret_id, name, integration, purpose, now),
                )
                connection.execute(
                    "INSERT INTO vault_values (secret_id, nonce, ciphertext) VALUES (?, ?, ?)",
                    (secret_id, nonce, ciphertext),
                )
        except (OSError, RuntimePathError, MetadataStoreError, InvalidTag, ValueError):
            raise VaultLockedError() from None
        return secret_id

    def update(self, secret_id: str, name: str, purpose: str, value: str) -> None:
        if not isinstance(value, str) or not value or len(value) > MAX_SECRET_LENGTH:
            raise VaultAccessError()
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT name, integration, purpose FROM vault_metadata WHERE secret_id = ?",
                (secret_id,),
            ).fetchone()
            if row is None or row[2] != purpose:
                raise VaultAccessError()
            integration = row[1]
            _validate_metadata(name, integration, purpose)
            nonce = secrets.token_bytes(12)
            key = self._root_key_for_write(connection)
            ciphertext = self.cipher.encrypt(
                key,
                nonce,
                value.encode("utf-8"),
                _associated_data(secret_id, name, integration, purpose),
            )
            connection.execute(
                "UPDATE vault_metadata SET name = ? WHERE secret_id = ?",
                (name, secret_id),
            )
            connection.execute(
                "UPDATE vault_values SET nonce = ?, ciphertext = ? WHERE secret_id = ?",
                (nonce, ciphertext, secret_id),
            )
            connection.execute("DELETE FROM vault_reveals WHERE secret_id = ?", (secret_id,))

    def rename(self, secret_id: str, name: str, purpose: str) -> None:
        value = self.get_for_service(secret_id, purpose)
        self.update(secret_id, name, purpose, value)

    def delete(self, secret_id: str, purpose: str) -> None:
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT purpose FROM vault_metadata WHERE secret_id = ?",
                (secret_id,),
            ).fetchone()
            if row is None or row[0] != purpose:
                raise VaultAccessError()
            connection.execute("DELETE FROM vault_metadata WHERE secret_id = ?", (secret_id,))

    def metadata(self) -> list[dict[str, str]]:
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT secret_id, name, integration, purpose, created_at "
                "FROM vault_metadata ORDER BY created_at, secret_id"
            ).fetchall()
        return [
            {
                "secret_id": row[0],
                "name": row[1],
                "integration": row[2],
                "purpose": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]

    def get_for_service(self, secret_id: str, purpose: str) -> str:
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT m.name, m.integration, m.purpose, v.nonce, v.ciphertext "
                "FROM vault_metadata AS m JOIN vault_values AS v USING (secret_id) "
                "WHERE m.secret_id = ?",
                (secret_id,),
            ).fetchone()
        if row is None or row[2] != purpose:
            raise VaultAccessError()
        return self._decrypt(secret_id, row[0], row[1], row[2], row[3], row[4])

    def issue_reveal(self, secret_id: str) -> str:
        token = self.token_factory(32)
        now = _as_utc(self.clock())
        with self.store.transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM vault_metadata WHERE secret_id = ?",
                (secret_id,),
            ).fetchone()
            if exists is None:
                raise VaultAccessError()
            connection.execute(
                "INSERT INTO vault_reveals (token_digest, secret_id, expires_at) VALUES (?, ?, ?)",
                (_digest(token), secret_id, _format_time(now + REVEAL_LIFETIME)),
            )
        return token

    def consume_reveal(self, reveal_token: str) -> str:
        now = _format_time(_as_utc(self.clock()))
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT r.secret_id, r.expires_at, r.consumed_at, m.name, m.integration, m.purpose, "
                "v.nonce, v.ciphertext FROM vault_reveals AS r "
                "JOIN vault_metadata AS m USING (secret_id) "
                "JOIN vault_values AS v USING (secret_id) WHERE r.token_digest = ?",
                (_digest(reveal_token),),
            ).fetchone()
            if row is None or row[2] is not None or row[1] <= now:
                raise VaultAccessError()
            connection.execute(
                "UPDATE vault_reveals SET consumed_at = ? WHERE token_digest = ? AND consumed_at IS NULL",
                (now, _digest(reveal_token)),
            )
        return self._decrypt(row[0], row[3], row[4], row[5], row[6], row[7])

    def lock_state(self) -> dict[str, str]:
        if not self.key_file.is_file():
            try:
                with self.store.transaction() as connection:
                    has_values = connection.execute(
                        "SELECT 1 FROM vault_values LIMIT 1"
                    ).fetchone()
            except MetadataStoreError:
                return {"state": "locked"}
            return {"state": "locked" if has_values else "uninitialized"}
        try:
            self._read_root_key()
        except VaultLockedError:
            return {"state": "locked"}
        return {"state": "unlocked"}

    def _root_key_for_write(self, connection) -> bytes:
        if self.key_file.is_file():
            return self._read_root_key()
        has_values = connection.execute("SELECT 1 FROM vault_values LIMIT 1").fetchone()
        if has_values is not None:
            raise VaultLockedError()
        key = secrets.token_bytes(32)
        protected = self.protector.protect(key)
        _write_protected_key(self.key_file, protected)
        return key

    def _read_root_key(self) -> bytes:
        try:
            protected = self.key_file.read_bytes()
            key = self.protector.unprotect(protected)
        except (OSError, VaultLockedError):
            raise VaultLockedError() from None
        if len(key) != 32:
            raise VaultLockedError()
        return key

    def _decrypt(
        self,
        secret_id: str,
        name: str,
        integration: str,
        purpose: str,
        nonce: bytes,
        ciphertext: bytes,
    ) -> str:
        key = self._read_root_key()
        try:
            plaintext = self.cipher.decrypt(
                key,
                nonce,
                ciphertext,
                _associated_data(secret_id, name, integration, purpose),
            )
            return plaintext.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, ValueError, TypeError):
            raise VaultLockedError() from None


def _write_protected_key(path: Path, protected: bytes) -> None:
    try:
        ensure_private_directory(path.parent)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(protected)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    except (OSError, RuntimePathError):
        raise VaultLockedError() from None


def _validate_metadata(name: str, integration: str, purpose: str) -> None:
    for item in (name, integration, purpose):
        if not isinstance(item, str) or not item.strip() or len(item) > 128:
            raise VaultAccessError()


def _associated_data(secret_id: str, name: str, integration: str, purpose: str) -> bytes:
    return json.dumps(
        [secret_id, name, integration, purpose],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_time(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")
