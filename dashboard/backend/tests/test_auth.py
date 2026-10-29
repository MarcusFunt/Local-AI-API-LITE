from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.auth import Argon2PasswordHasher, AuthenticationError, AuthService
from app.persistence import MetadataStore

PASSWORD = "Pass1234"


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 10, 29, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class TokenFactory:
    def __init__(self):
        self.counter = 0

    def __call__(self, size: int = 32) -> str:
        self.counter += 1
        return f"token-{size}-{self.counter:04d}"


class FakePasswordHasher:
    def hash(self, password: str) -> str:
        return f"$argon2id$test${password}"

    def verify(self, password: str, verifier: str) -> bool:
        return verifier == self.hash(password)


class CountingPasswordHasher(FakePasswordHasher):
    def __init__(self):
        self.hash_calls = 0

    def hash(self, password: str) -> str:
        self.hash_calls += 1
        return super().hash(password)


def _service(path, *, clock=None, hasher=None) -> AuthService:
    return AuthService(
        MetadataStore(path),
        clock=clock or MutableClock(),
        token_factory=TokenFactory(),
        password_hasher=hasher or FakePasswordHasher(),
        limiter=None,
    )


def _create_admin(service: AuthService) -> None:
    service.setup(service.issue_bootstrap(), PASSWORD)


def test_bootstrap_token_is_unique_and_single_use(tmp_path):
    service = _service(tmp_path / "metadata.sqlite3")
    first = service.issue_bootstrap()
    second = service.issue_bootstrap()

    assert first != second
    with pytest.raises(AuthenticationError):
        service.setup(first, PASSWORD)
    service.setup(second, PASSWORD)
    with pytest.raises(AuthenticationError):
        service.setup(second, PASSWORD)


def test_invalid_bootstrap_token_does_not_hash_candidate_password(tmp_path):
    hasher = CountingPasswordHasher()
    service = _service(tmp_path / "metadata.sqlite3", hasher=hasher)

    with pytest.raises(AuthenticationError):
        service.setup("invalid-token", PASSWORD)

    assert hasher.hash_calls == 0


def test_invalid_recovery_token_does_not_hash_candidate_password(tmp_path):
    hasher = CountingPasswordHasher()
    service = _service(tmp_path / "metadata.sqlite3", hasher=hasher)
    _create_admin(service)
    hasher.hash_calls = 0

    with pytest.raises(AuthenticationError):
        service.recover_with_local_token("invalid-token", "RecoveredStrongPassword7")

    assert hasher.hash_calls == 0


def test_setup_requires_new_password_and_creates_argon2id_verifier(tmp_path):
    service = _service(
        tmp_path / "metadata.sqlite3",
        hasher=Argon2PasswordHasher(),
    )
    token = service.issue_bootstrap()

    with pytest.raises(AuthenticationError):
        service.setup(token, "short")

    service.setup(token, PASSWORD)
    with service.store.transaction() as connection:
        verifier = connection.execute(
            "SELECT password_hash FROM admin_account WHERE singleton = 1"
        ).fetchone()[0]
        saved_values = " ".join(str(value) for row in connection.execute(
            "SELECT * FROM auth_tokens"
        ) for value in row)

    assert verifier.startswith("$argon2id$")
    assert PASSWORD not in verifier
    assert token not in saved_values


def test_setup_accepts_eight_character_password_without_symbol(tmp_path):
    service = _service(tmp_path / "metadata.sqlite3")
    token = service.issue_bootstrap()

    service.setup(token, "Abcd1234")

    assert service.login("Abcd1234", "127.0.0.1")


@pytest.mark.parametrize(
    "password",
    ["lowercasepassword1", "UPPERCASEPASSWORD1", "PasswordWithoutDigits"],
    ids=["uppercase-required", "lowercase-required", "number-required"],
)
def test_setup_rejects_password_missing_a_required_character_class(password, tmp_path):
    service = _service(tmp_path / "metadata.sqlite3")
    token = service.issue_bootstrap()

    with pytest.raises(AuthenticationError):
        service.setup(token, password)


def test_login_rotates_session_tokens(tmp_path):
    service = _service(tmp_path / "metadata.sqlite3")
    _create_admin(service)

    first = service.login(PASSWORD, "127.0.0.1")
    second = service.login(PASSWORD, "127.0.0.1")

    assert first.session_token != second.session_token
    assert first.csrf_token != second.csrf_token
    assert first.session.session_id != second.session.session_id


def test_session_token_is_hashed_in_sqlite(tmp_path):
    service = _service(tmp_path / "metadata.sqlite3")
    _create_admin(service)
    login = service.login(PASSWORD, "127.0.0.1")

    with service.store.transaction() as connection:
        row = connection.execute(
            "SELECT token_digest FROM auth_sessions WHERE session_id = ?",
            (login.session.session_id,),
        ).fetchone()

    assert row is not None
    assert login.session_token not in row[0]
    assert len(row[0]) == 64


def test_expired_and_revoked_sessions_are_rejected(tmp_path):
    clock = MutableClock()
    service = _service(tmp_path / "metadata.sqlite3", clock=clock)
    _create_admin(service)
    expired = service.login(PASSWORD, "127.0.0.1")
    clock.advance(timedelta(hours=9))
    assert service.load_session(expired.session_token) is None

    live = service.login(PASSWORD, "127.0.0.1")
    session = service.load_session(live.session_token)
    assert session is not None
    service.logout(session)
    assert service.load_session(live.session_token) is None


def test_logout_revokes_server_session(tmp_path):
    service = _service(tmp_path / "metadata.sqlite3")
    _create_admin(service)
    login = service.login(PASSWORD, "127.0.0.1")

    service.logout(login.session)

    assert service.load_session(login.session_token) is None


def test_password_change_revokes_old_sessions(tmp_path):
    service = _service(tmp_path / "metadata.sqlite3")
    _create_admin(service)
    first = service.login(PASSWORD, "127.0.0.1")
    second = service.login(PASSWORD, "127.0.0.1")

    service.change_password(first.session, PASSWORD, "DifferentStrongPassword7")

    assert service.load_session(first.session_token) is None
    assert service.load_session(second.session_token) is None
    assert service.login("DifferentStrongPassword7", "127.0.0.1")


def test_login_rate_limit_survives_app_restart(tmp_path):
    database_path = tmp_path / "metadata.sqlite3"
    service = _service(database_path)
    _create_admin(service)
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            service.login("wrong password", "192.0.2.10")

    restarted = _service(database_path)

    with pytest.raises(AuthenticationError, match="Authentication failed"):
        restarted.login(PASSWORD, "192.0.2.10")


def test_auth_failures_are_generic(tmp_path):
    service = _service(tmp_path / "metadata.sqlite3")
    with pytest.raises(AuthenticationError) as no_account:
        service.login(PASSWORD, "client-without-account")
    _create_admin(service)
    with pytest.raises(AuthenticationError) as wrong_password:
        service.login("incorrect password", "different-client")

    assert str(no_account.value) == str(wrong_password.value) == "Authentication failed"


def test_recovery_token_is_one_use_and_revokes_sessions(tmp_path):
    service = _service(tmp_path / "metadata.sqlite3")
    _create_admin(service)
    login = service.login(PASSWORD, "127.0.0.1")
    recovery_token = service.issue_recovery()

    assert service.load_session(login.session_token) is None
    service.recover_with_local_token(recovery_token, "RecoveredStrongPassword7")
    assert service.login("RecoveredStrongPassword7", "127.0.0.1")
    with pytest.raises(AuthenticationError):
        service.recover_with_local_token(recovery_token, "AnotherStrongPassword8")
