from __future__ import annotations

from io import StringIO

import pytest
from conftest import load_dashboard_module


def test_recovery_cli_revokes_sessions_and_preserves_vault_data(monkeypatch, tmp_path):
    bootstrap = load_dashboard_module("bootstrap")
    auth = load_dashboard_module("auth")
    persistence = load_dashboard_module("persistence")
    secrets_store = load_dashboard_module("secrets_store")
    store = persistence.MetadataStore(tmp_path / "metadata.sqlite3")
    service = auth.AuthService(store)
    service.setup("LongPasswordForDashboard7")
    login = service.login("LongPasswordForDashboard7", "127.0.0.1")
    control_state = load_dashboard_module("control_state")
    jobs = load_dashboard_module("jobs")
    store.migrate(3, control_state.CONTROL_SCHEMA)
    store.migrate(4, jobs.JOB_SCHEMA)
    store.migrate(5, secrets_store.VAULT_SCHEMA)
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO vault_metadata (secret_id, name, integration, purpose, created_at) "
            "VALUES ('synthetic', 'history', 'postgres', 'monitor', '2026-10-29T10:00:00Z')"
        )
        connection.execute(
            "INSERT INTO vault_values (secret_id, nonce, ciphertext) VALUES ('synthetic', ?, ?)",
            (b"n" * 12, b"encrypted synthetic value"),
        )
    monkeypatch.setattr(bootstrap, "_service", lambda: service)
    console = StringIO()
    monkeypatch.setattr("sys.stdout", console)

    assert bootstrap.main(["recover-admin"]) == 0
    recovery_token = console.getvalue().strip()

    assert recovery_token
    assert service.load_session(login.session_token) is None
    with store.transaction() as connection:
        vault_value = connection.execute(
            "SELECT ciphertext FROM vault_values WHERE secret_id = 'synthetic'"
        ).fetchone()[0]
        stored_token = connection.execute(
            "SELECT token_digest FROM auth_tokens WHERE purpose = 'recovery'"
        ).fetchone()[0]
    assert vault_value == b"encrypted synthetic value"
    assert recovery_token not in stored_token
    service.recover_with_local_token(recovery_token, "NewLongPasswordForDashboard7")
    with pytest.raises(auth.AuthenticationError):
        service.recover_with_local_token(recovery_token, "AnotherNewLongPassword8")
