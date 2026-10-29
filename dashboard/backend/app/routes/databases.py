from __future__ import annotations

import re
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app.auth import AuthSession, require_csrf, require_session
from app.databases import DATABASE_MONITOR_INTEGRATION, DATABASE_MONITOR_PURPOSE
from app.persistence import MetadataStoreError
from app.secrets_store import VaultAccessError, VaultLockedError

router = APIRouter(prefix="/api/control/v1/databases", tags=["databases"])
_DATABASE_ID = re.compile(r"^[0-9a-f]{32}$")
_FAILURES = {
    "connection_error",
    "query_error",
    "secret_unavailable",
    "vault_locked",
    "probe_timeout",
    "probe_busy",
    "not_configured",
}


class DatabaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    dsn: str = Field(min_length=1, max_length=4096)


class DatabaseUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    dsn: str | None = Field(default=None, min_length=1, max_length=4096)


def _clean_name(raw: str) -> str:
    name = raw.strip()
    if not name or len(name) > 80 or any(ord(character) < 32 for character in name):
        raise HTTPException(status_code=422, detail="Invalid database monitor name")
    return name


def _validate_dsn(dsn: str) -> str:
    if len(dsn) > 4096 or any(character.isspace() or ord(character) < 32 for character in dsn):
        raise HTTPException(status_code=422, detail="Invalid PostgreSQL connection string")
    try:
        parsed = urlsplit(dsn)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            raise ValueError
        _ = parsed.port
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid PostgreSQL connection string") from None
    return dsn


def _database_metadata(request: Request, database_id: str) -> dict[str, str] | None:
    if not _DATABASE_ID.fullmatch(database_id):
        return None
    for item in request.app.state.secret_store.metadata():
        if not isinstance(item, dict):
            continue
        if (
            item.get("secret_id") == database_id
            and item.get("integration") == DATABASE_MONITOR_INTEGRATION
            and item.get("purpose") == DATABASE_MONITOR_PURPOSE
        ):
            return item
    return None


def _public_status(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"state": "Unavailable", "checked_at": None, "latency_ms": None, "failure_category": "connection_error"}
    database_id = raw.get("database_id")
    if not isinstance(database_id, str) or not _DATABASE_ID.fullmatch(database_id):
        raise HTTPException(status_code=503, detail="Database monitor status is unavailable")
    state = raw.get("state")
    if state not in {"Unknown", "Unconfigured", "Configured", "Available", "Unavailable"}:
        state = "Unknown"
    latency = raw.get("latency_ms")
    if type(latency) is not int or not 0 <= latency <= 600_000:
        latency = None
    category = raw.get("failure_category")
    if not isinstance(category, str) or category not in _FAILURES:
        category = None
    checked_at = raw.get("checked_at") if isinstance(raw.get("checked_at"), str) else None
    result = {
        "database_id": database_id,
        "state": state,
        "checked_at": checked_at,
        "latency_ms": latency,
        "failure_category": category,
    }
    name = raw.get("name")
    if isinstance(name, str) and len(name) <= 80:
        result["name"] = name
    return result


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Database settings are unavailable")


@router.get("")
def list_databases(
    request: Request,
    _session: Annotated[AuthSession, Depends(require_session)],
) -> dict[str, Any]:
    try:
        raw = request.app.state.database_monitor.list_status()
    except (MetadataStoreError, OSError, RuntimeError, TypeError, ValueError):
        raise _unavailable() from None
    if not isinstance(raw, dict) or not isinstance(raw.get("databases"), list):
        raise _unavailable()
    config = raw.get("configuration_state")
    config_state = config.get("state") if isinstance(config, dict) else None
    if config_state not in {"Unconfigured", "Configured", "Unavailable"}:
        config_state = "Unavailable"
    databases = [
        public
        for item in raw["databases"]
        if isinstance(item, dict)
        for public in [_public_status(item)]
    ]
    return {"configuration_state": {"state": config_state}, "databases": databases}


@router.post("", status_code=201)
def create_database(
    body: DatabaseCreateRequest,
    request: Request,
    _session: Annotated[AuthSession, Depends(require_csrf)],
) -> dict[str, str]:
    name = _clean_name(body.name)
    dsn = _validate_dsn(body.dsn)
    try:
        database_id = request.app.state.secret_store.put(
            name,
            DATABASE_MONITOR_INTEGRATION,
            DATABASE_MONITOR_PURPOSE,
            dsn,
        )
    except (VaultAccessError, VaultLockedError, MetadataStoreError, OSError, RuntimeError):
        raise _unavailable() from None
    return {"database_id": database_id, "name": name, "state": "Unknown"}


@router.put("/{database_id}")
def update_database(
    database_id: str,
    body: DatabaseUpdateRequest,
    request: Request,
    _session: Annotated[AuthSession, Depends(require_csrf)],
) -> dict[str, str]:
    item = _database_metadata(request, database_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Database monitor was not found")
    name = _clean_name(body.name)
    try:
        if body.dsn is None:
            request.app.state.secret_store.rename(database_id, name, DATABASE_MONITOR_PURPOSE)
        else:
            dsn = _validate_dsn(body.dsn)
            request.app.state.secret_store.update(database_id, name, DATABASE_MONITOR_PURPOSE, dsn)
    except (VaultAccessError, VaultLockedError, MetadataStoreError, OSError, RuntimeError):
        raise _unavailable() from None
    return {"database_id": database_id, "name": name}


@router.delete("/{database_id}", status_code=204)
def delete_database(
    database_id: str,
    request: Request,
    _session: Annotated[AuthSession, Depends(require_csrf)],
) -> Response:
    if _database_metadata(request, database_id) is None:
        raise HTTPException(status_code=404, detail="Database monitor was not found")
    try:
        request.app.state.secret_store.delete(database_id, DATABASE_MONITOR_PURPOSE)
    except (VaultAccessError, VaultLockedError, MetadataStoreError, OSError, RuntimeError):
        raise _unavailable() from None
    return Response(status_code=204)


@router.post("/{database_id}/check")
def check_database(
    database_id: str,
    request: Request,
    _session: Annotated[AuthSession, Depends(require_csrf)],
) -> dict[str, Any]:
    item = _database_metadata(request, database_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Database monitor was not found")
    try:
        result = _public_status(request.app.state.database_monitor.check(database_id))
        result["name"] = item["name"]
        return result
    except (MetadataStoreError, OSError, RuntimeError, TypeError, ValueError):
        raise HTTPException(status_code=503, detail="Database probe is unavailable") from None


@router.post("/{database_id}/reveal")
def reveal_database_credential(
    database_id: str,
    request: Request,
    _session: Annotated[AuthSession, Depends(require_csrf)],
) -> JSONResponse:
    if _database_metadata(request, database_id) is None:
        raise HTTPException(status_code=404, detail="Database monitor was not found")
    try:
        token = request.app.state.secret_store.issue_reveal(database_id)
        credential = request.app.state.secret_store.consume_reveal(token)
    except (VaultAccessError, VaultLockedError, MetadataStoreError, OSError, RuntimeError):
        raise _unavailable() from None
    return JSONResponse(
        content={"credential": credential},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
