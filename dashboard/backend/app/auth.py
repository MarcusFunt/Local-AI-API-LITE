from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from fastapi import Depends, HTTPException, Request

from app.persistence import MetadataStore

SESSION_LIFETIME = timedelta(hours=8)
TOKEN_LIFETIME = timedelta(minutes=30)
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_FAILURE_LIMIT = 5

AUTH_SCHEMA = [
    (
        "CREATE TABLE admin_account ("
        "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
        "password_hash TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    ),
    (
        "CREATE TABLE auth_tokens ("
        "token_digest TEXT PRIMARY KEY, purpose TEXT NOT NULL CHECK (purpose IN ('setup', 'recovery')), "
        "created_at TEXT NOT NULL, expires_at TEXT NOT NULL, consumed_at TEXT)"
    ),
    (
        "CREATE TABLE auth_sessions ("
        "session_id TEXT PRIMARY KEY, token_digest TEXT NOT NULL UNIQUE, csrf_digest TEXT NOT NULL, "
        "created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT)"
    ),
    "CREATE INDEX auth_sessions_active ON auth_sessions (expires_at, revoked_at)",
    "CREATE TABLE login_failures (client_digest TEXT NOT NULL, attempted_at TEXT NOT NULL)",
    "CREATE INDEX login_failures_window ON login_failures (client_digest, attempted_at)",
]


class AuthenticationError(RuntimeError):
    """A deliberately generic authentication failure."""

    def __init__(self) -> None:
        super().__init__("Authentication failed")


class Argon2PasswordHasher:
    def __init__(self) -> None:
        self._hasher = PasswordHasher()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password: str, verifier: str) -> bool:
        try:
            return self._hasher.verify(verifier, password)
        except (Argon2Error, InvalidHashError):
            return False


@dataclass(frozen=True, slots=True)
class AuthSession:
    session_id: str
    token_digest: str
    csrf_digest: str
    created_at: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class LoginResult:
    session_token: str
    csrf_token: str
    session: AuthSession


class PersistentLoginLimiter:
    def __init__(self, store: MetadataStore, clock: Callable[[], datetime]) -> None:
        self.store = store
        self.clock = clock

    def is_limited(self, client_key: str) -> bool:
        digest = _digest(client_key)
        cutoff = _format_time(_as_utc(self.clock()) - LOGIN_WINDOW)
        with self.store.transaction() as connection:
            connection.execute("DELETE FROM login_failures WHERE attempted_at < ?", (cutoff,))
            count = connection.execute(
                "SELECT COUNT(*) FROM login_failures WHERE client_digest = ? AND attempted_at >= ?",
                (digest, cutoff),
            ).fetchone()[0]
        return count >= LOGIN_FAILURE_LIMIT

    def record_failure(self, client_key: str) -> None:
        digest = _digest(client_key)
        now = _format_time(_as_utc(self.clock()))
        cutoff = _format_time(_as_utc(self.clock()) - LOGIN_WINDOW)
        with self.store.transaction() as connection:
            connection.execute("DELETE FROM login_failures WHERE attempted_at < ?", (cutoff,))
            count = connection.execute(
                "SELECT COUNT(*) FROM login_failures WHERE client_digest = ? AND attempted_at >= ?",
                (digest, cutoff),
            ).fetchone()[0]
            if count < LOGIN_FAILURE_LIMIT:
                connection.execute(
                    "INSERT INTO login_failures (client_digest, attempted_at) VALUES (?, ?)",
                    (digest, now),
                )

    def clear(self, client_key: str) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                "DELETE FROM login_failures WHERE client_digest = ?",
                (_digest(client_key),),
            )


class AuthService:
    def __init__(
        self,
        store: MetadataStore,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[int], str] | None = None,
        password_hasher: object | None = None,
        limiter: PersistentLoginLimiter | None = None,
    ) -> None:
        self.store = store
        self.clock = clock or (lambda: datetime.now(UTC))
        self.token_factory = token_factory or (lambda size: secrets.token_urlsafe(size))
        self.password_hasher = password_hasher or Argon2PasswordHasher()
        self.limiter = limiter or PersistentLoginLimiter(store, self.clock)
        self.store.migrate(2, AUTH_SCHEMA)

    def is_setup_required(self) -> bool:
        with self.store.transaction() as connection:
            count = connection.execute("SELECT COUNT(*) FROM admin_account").fetchone()[0]
        return count == 0

    def issue_bootstrap(self) -> str:
        if not self.is_setup_required():
            raise AuthenticationError()
        return self._issue_local_token("setup")

    def issue_recovery(self) -> str:
        now = _format_time(_as_utc(self.clock()))
        token = self._new_token()
        with self.store.transaction() as connection:
            exists = connection.execute("SELECT 1 FROM admin_account WHERE singleton = 1").fetchone()
            if exists is None:
                raise AuthenticationError()
            connection.execute(
                "UPDATE auth_tokens SET consumed_at = ? WHERE purpose = 'recovery' AND consumed_at IS NULL",
                (now,),
            )
            connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE revoked_at IS NULL",
                (now,),
            )
            connection.execute(
                "INSERT INTO auth_tokens (token_digest, purpose, created_at, expires_at) "
                "VALUES (?, 'recovery', ?, ?)",
                (_digest(token), now, _format_time(_as_utc(self.clock()) + TOKEN_LIFETIME)),
            )
        return token

    def setup(self, token: str, password: str) -> None:
        _validate_password(password)
        now = _format_time(_as_utc(self.clock()))
        with self.store.transaction() as connection:
            exists = connection.execute("SELECT 1 FROM admin_account WHERE singleton = 1").fetchone()
            token_row = connection.execute(
                "SELECT purpose, expires_at, consumed_at FROM auth_tokens WHERE token_digest = ?",
                (_digest(token),),
            ).fetchone()
            if (
                exists is not None
                or token_row is None
                or token_row[0] != "setup"
                or token_row[2] is not None
                or token_row[1] <= now
            ):
                raise AuthenticationError()
            verifier = _safe_hash(self.password_hasher, password)
            connection.execute(
                "INSERT INTO admin_account (singleton, password_hash, created_at, updated_at) "
                "VALUES (1, ?, ?, ?)",
                (verifier, now, now),
            )
            connection.execute(
                "UPDATE auth_tokens SET consumed_at = ? WHERE token_digest = ?",
                (now, _digest(token)),
            )

    def login(self, password: str, client_key: str) -> LoginResult:
        if self.limiter.is_limited(client_key):
            raise AuthenticationError()
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT password_hash FROM admin_account WHERE singleton = 1"
            ).fetchone()
        verifier = row[0] if row is not None else None
        if verifier is None or not _safe_verify(self.password_hasher, password, verifier):
            self.limiter.record_failure(client_key)
            raise AuthenticationError()

        self.limiter.clear(client_key)
        now_dt = _as_utc(self.clock())
        now = _format_time(now_dt)
        expires = _format_time(now_dt + SESSION_LIFETIME)
        session_token = self._new_token()
        csrf_token = self._new_token()
        session = AuthSession(
            session_id=uuid4().hex,
            token_digest=_digest(session_token),
            csrf_digest=_digest(csrf_token),
            created_at=now,
            expires_at=expires,
        )
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO auth_sessions (session_id, token_digest, csrf_digest, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    session.session_id,
                    session.token_digest,
                    session.csrf_digest,
                    session.created_at,
                    session.expires_at,
                ),
            )
        return LoginResult(session_token, csrf_token, session)

    def load_session(self, cookie: str | None) -> AuthSession | None:
        if not cookie:
            return None
        now = _format_time(_as_utc(self.clock()))
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT session_id, token_digest, csrf_digest, created_at, expires_at, revoked_at "
                "FROM auth_sessions WHERE token_digest = ?",
                (_digest(cookie),),
            ).fetchone()
        if row is None or row[5] is not None or row[4] <= now:
            return None
        return AuthSession(row[0], row[1], row[2], row[3], row[4])

    def list_sessions(self, session: AuthSession) -> list[dict[str, object]]:
        now = _format_time(_as_utc(self.clock()))
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT session_id, created_at, expires_at FROM auth_sessions "
                "WHERE revoked_at IS NULL AND expires_at > ? ORDER BY created_at DESC",
                (now,),
            ).fetchall()
        return [
            {
                "id": row[0],
                "created_at": row[1],
                "expires_at": row[2],
                "current": row[0] == session.session_id,
            }
            for row in rows
        ]

    def revoke_other_sessions(self, session: AuthSession) -> int:
        now = _format_time(_as_utc(self.clock()))
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? "
                "WHERE session_id <> ? AND revoked_at IS NULL",
                (now, session.session_id),
            )
        return cursor.rowcount

    def logout(self, session: AuthSession) -> None:
        now = _format_time(_as_utc(self.clock()))
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
                (now, session.session_id),
            )

    def change_password(self, session: AuthSession, old_password: str, new_password: str) -> None:
        _validate_password(new_password)
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT password_hash FROM admin_account WHERE singleton = 1"
            ).fetchone()
            active = connection.execute(
                "SELECT 1 FROM auth_sessions WHERE session_id = ? AND revoked_at IS NULL "
                "AND expires_at > ?",
                (session.session_id, _format_time(_as_utc(self.clock()))),
            ).fetchone()
            if row is None or active is None or not _safe_verify(
                self.password_hasher, old_password, row[0]
            ):
                raise AuthenticationError()
            verifier = _safe_hash(self.password_hasher, new_password)
            now = _format_time(_as_utc(self.clock()))
            connection.execute(
                "UPDATE admin_account SET password_hash = ?, updated_at = ? WHERE singleton = 1",
                (verifier, now),
            )
            connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE revoked_at IS NULL",
                (now,),
            )

    def recover_with_local_token(self, token: str, new_password: str) -> None:
        _validate_password(new_password)
        now = _format_time(_as_utc(self.clock()))
        with self.store.transaction() as connection:
            token_row = connection.execute(
                "SELECT expires_at, consumed_at FROM auth_tokens "
                "WHERE token_digest = ? AND purpose = 'recovery'",
                (_digest(token),),
            ).fetchone()
            if token_row is None or token_row[1] is not None or token_row[0] <= now:
                raise AuthenticationError()
            verifier = _safe_hash(self.password_hasher, new_password)
            connection.execute(
                "UPDATE admin_account SET password_hash = ?, updated_at = ? WHERE singleton = 1",
                (verifier, now),
            )
            connection.execute(
                "UPDATE auth_tokens SET consumed_at = ? WHERE token_digest = ?",
                (now, _digest(token)),
            )
            connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE revoked_at IS NULL",
                (now,),
            )

    def csrf_matches(self, session: AuthSession, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(session.csrf_digest, _digest(token))

    def _issue_local_token(self, purpose: str) -> str:
        token = self._new_token()
        now_dt = _as_utc(self.clock())
        now = _format_time(now_dt)
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE auth_tokens SET consumed_at = ? WHERE purpose = ? AND consumed_at IS NULL",
                (now, purpose),
            )
            connection.execute(
                "INSERT INTO auth_tokens (token_digest, purpose, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (_digest(token), purpose, now, _format_time(now_dt + TOKEN_LIFETIME)),
            )
        return token

    def _new_token(self) -> str:
        return self.token_factory(32)


def require_session(request: Request) -> AuthSession:
    service: AuthService = request.app.state.auth_service
    session = service.load_session(request.cookies.get("dashboard_session"))
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return session


def require_csrf(
    request: Request,
    session: Annotated[AuthSession, Depends(require_session)],
) -> AuthSession:
    validate_local_request(request)
    service: AuthService = request.app.state.auth_service
    if not service.csrf_matches(session, request.headers.get("X-CSRF-Token")):
        raise HTTPException(status_code=403, detail="Request verification failed")
    return session


def validate_local_request(request: Request) -> None:
    host = request.headers.get("host", "")
    origin = request.headers.get("origin", "")
    try:
        origin_url = urlsplit(origin)
        request_url = request.url
        valid = (
            request_url.hostname in {"localhost", "127.0.0.1", "::1"}
            and origin_url.hostname in {"localhost", "127.0.0.1", "::1"}
            and origin_url.netloc.casefold() == host.casefold()
            and origin_url.scheme == request_url.scheme
            and not origin_url.username
            and not origin_url.password
            and origin_url.path in {"", "/"}
            and not origin_url.query
            and not origin_url.fragment
        )
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(status_code=403, detail="Request verification failed")


def _validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < 12 or len(password) > 256:
        raise AuthenticationError()


def _safe_hash(hasher: object, password: str) -> str:
    try:
        return hasher.hash(password)
    except Argon2Error:
        raise AuthenticationError() from None


def _safe_verify(hasher: object, password: str, verifier: str) -> bool:
    try:
        return bool(hasher.verify(password, verifier))
    except (Argon2Error, InvalidHashError):
        return False


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_time(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")
