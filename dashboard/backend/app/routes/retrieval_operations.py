from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.auth import AuthSession, require_csrf, require_session

router = APIRouter(prefix="/api/control/v1/retrieval-operations", tags=["retrieval-operations"])


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Literal[True]


def _coordinator(request: Request):
    return request.app.state.retrieval_operation_coordinator


def _operation_response(record: dict) -> JSONResponse:
    return JSONResponse(status_code=409 if record.get("phase") == "rejected" else 200, content=record)


@router.get("/state")
def retrieval_operation_state(
    request: Request,
    _session: Annotated[AuthSession, Depends(require_session)],
) -> dict:
    return _coordinator(request).state()


def _run(
    request: Request,
    service: Literal["embed", "rerank"],
    action: Literal["start", "stop", "restart"],
    body: ConfirmRequest,
    session: AuthSession,
) -> JSONResponse:
    if body.confirm is not True:
        raise HTTPException(status_code=422, detail="Operation confirmation is required")
    coordinator = _coordinator(request)
    if not coordinator.configured():
        return JSONResponse(
            status_code=409,
            content={"message": "Retrieval controls are unavailable until an operator explicitly configures the retrieval Compose project."},
        )
    try:
        record = getattr(coordinator, action)(service, actor_session_id=session.session_id)
    except (KeyError, ValueError):
        raise HTTPException(status_code=422, detail="Retrieval service is not allowlisted") from None
    return _operation_response(record)


@router.post("/{service}/start")
def start(
    service: Literal["embed", "rerank"],
    body: ConfirmRequest,
    request: Request,
    session: Annotated[AuthSession, Depends(require_csrf)],
) -> JSONResponse:
    return _run(request, service, "start", body, session)


@router.post("/{service}/stop")
def stop(
    service: Literal["embed", "rerank"],
    body: ConfirmRequest,
    request: Request,
    session: Annotated[AuthSession, Depends(require_csrf)],
) -> JSONResponse:
    return _run(request, service, "stop", body, session)


@router.post("/{service}/restart")
def restart(
    service: Literal["embed", "rerank"],
    body: ConfirmRequest,
    request: Request,
    session: Annotated[AuthSession, Depends(require_csrf)],
) -> JSONResponse:
    return _run(request, service, "restart", body, session)
