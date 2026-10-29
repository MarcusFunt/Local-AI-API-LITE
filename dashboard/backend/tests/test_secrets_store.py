from __future__ import annotations

import os

import pytest
from fakes import FakeKeyProtector

from app.auth import AuthService
from app.control_state import CONTROL_SCHEMA
from app.jobs import JOB_SCHEMA
from app.persistence import MetadataStore
from app.secrets_store import (
    AESGCMCipher,
    SecretsStore,
    VaultAccessError,
    VaultLockedError,
    WindowsDpapiProtector,
)

SECRET_VALUE = "postgresql://monitor:synthetic-password@db.example.invalid:5432/app"


def _vault(tmp_path, protector=None):
    metadata = MetadataStore(tmp_path / "metadata.sqlite3")
    AuthService(metadata)
    metadata.migrate(3, CONTROL_SCHEMA)
    metadata.migrate(4, JOB_SCHEMA)
    key_file = tmp_path / "vault" / "root-key.dpapi"
    protector = protector or FakeKeyProtector()
    return SecretsStore(metadata, key_file, protector, AESGCMCipher()), protector


def test_secret_value_is_authenticated_encrypted_and_metadata_separate(tmp_path):
    vault, protector = _vault(tmp_path)

    secret_id = vault.put("history-db", "postgres", "database-monitor", SECRET_VALUE)
    metadata = vault.metadata()
    with vault.store.transaction() as connection:
        stored_metadata = connection.execute(
            "SELECT name, integration, purpose FROM vault_metadata WHERE secret_id = ?",
            (secret_id,),
        ).fetchone()
        nonce, ciphertext = connection.execute(
            "SELECT nonce, ciphertext FROM vault_values WHERE secret_id = ?",
            (secret_id,),
        ).fetchone()

    assert stored_metadata == ("history-db", "postgres", "database-monitor")
    assert len(nonce) == 12
    assert ciphertext != SECRET_VALUE.encode()
    assert SECRET_VALUE not in str(metadata)
    assert SECRET_VALUE.encode() not in ciphertext
    assert vault.key_file.read_bytes() != protector.last_key
    assert vault.get_for_service(secret_id, "database-monitor") == SECRET_VALUE


def test_tampered_ciphertext_is_locked(tmp_path):
    vault, _ = _vault(tmp_path)
    secret_id = vault.put("history-db", "postgres", "database-monitor", SECRET_VALUE)
    with vault.store.transaction() as connection:
        connection.execute(
            "UPDATE vault_values SET ciphertext = ? WHERE secret_id = ?",
            (b"tampered", secret_id),
        )

    with pytest.raises(VaultLockedError):
        vault.get_for_service(secret_id, "database-monitor")


def test_missing_dpapi_key_is_locked_without_plaintext_fallback(tmp_path):
    vault, protector = _vault(tmp_path)
    secret_id = vault.put("history-db", "postgres", "database-monitor", SECRET_VALUE)
    vault.key_file.unlink()

    assert vault.lock_state()["state"] == "locked"
    with pytest.raises(VaultLockedError):
        vault.get_for_service(secret_id, "database-monitor")
    with pytest.raises(VaultLockedError):
        vault.put("other-db", "postgres", "database-monitor", "another secret")
    assert protector.last_key is not None


def test_secret_metadata_never_contains_value(tmp_path):
    vault, _ = _vault(tmp_path)
    vault.put("history-db", "postgres", "database-monitor", SECRET_VALUE)

    assert SECRET_VALUE not in repr(vault.metadata())
    with vault.store.transaction() as connection:
        metadata_rows = connection.execute("SELECT * FROM vault_metadata").fetchall()
    assert SECRET_VALUE not in repr(metadata_rows)


def test_reveal_token_is_one_use_and_stored_as_digest(tmp_path):
    vault, _ = _vault(tmp_path)
    secret_id = vault.put("history-db", "postgres", "database-monitor", SECRET_VALUE)
    reveal_token = vault.issue_reveal(secret_id)
    with vault.store.transaction() as connection:
        saved_digest = connection.execute(
            "SELECT token_digest FROM vault_reveals"
        ).fetchone()[0]

    assert reveal_token not in saved_digest
    assert len(saved_digest) == 64
    assert vault.consume_reveal(reveal_token) == SECRET_VALUE
    with pytest.raises(VaultAccessError):
        vault.consume_reveal(reveal_token)


def test_non_windows_tests_use_fake_key_protector(tmp_path):
    protector = FakeKeyProtector()
    vault, used_protector = _vault(tmp_path, protector)
    secret_id = vault.put("test", "fixture", "unit-test", "fake-protected-value")

    assert used_protector is protector
    assert vault.get_for_service(secret_id, "unit-test") == "fake-protected-value"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI CurrentUser is available only on Windows")
def test_windows_dpapi_roundtrip_current_user():
    protector = WindowsDpapiProtector()
    synthetic_key = bytes(range(32))

    protected = protector.protect(synthetic_key)

    assert protected != synthetic_key
    assert protector.unprotect(protected) == synthetic_key
