from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.auth import AuthSession, require_csrf, require_session
from app.control_state import OperationError, UnknownProfileError

router = APIRouter(prefix="/api/control/v1/operations", tags=["operations"])


class ProfileOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: Literal["baseline-4k-2", "baseline-8k-4"]


def _unconfigured() -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "message": "Inference controls are unavailable until a local Compose project is configured."
        },
    )


def _operation_response(record: dict) -> JSONResponse:
    status = 409 if record.get("phase") == "rejected" else 200
    return JSONResponse(status_code=status, content=record)


def _run(request: Request, action: str, profile_id: str | None = None) -> JSONResponse:
    settings = request.app.state.local_settings_store
    if settings.load_compose_root() is None:
        return _unconfigured()
    coordinator = request.app.state.operation_coordinator
    try:
        if action == "start":
            record = coordinator.start(profile_id)
        elif action == "apply_profile":
            record = coordinator.apply_profile(profile_id)
        else:
            record = getattr(coordinator, action)()
    except UnknownProfileError:
        raise HTTPException(status_code=422, detail="Requested profile is not available") from None
    except OperationError:
        raise HTTPException(status_code=409, detail="Inference operation could not be accepted") from None
    return _operation_response(record)


@router.get("/state")
def operation_state(
    request: Request,
    _session: Annotated[AuthSession, Depends(require_session)],
) -> dict:
    coordinator = request.app.state.operation_coordinator
    return {
        "configured": request.app.state.local_settings_store.load_compose_root() is not None,
        "control_state": coordinator.control_state.get_control_state(),
        "latest_operation": coordinator.control_state.latest_operation(),
    }


@router.post("/start")
def start(
    body: ProfileOperationRequest,
    request: Request,
    _session: Annotated[AuthSession, Depends(require_csrf)],
) -> JSONResponse:
    return _run(request, "start", body.profile_id)


@router.post("/stop")
def stop(
    request: Request,
    _session: Annotated[AuthSession, Depends(require_csrf)],
) -> JSONResponse:
    return _run(request, "stop")


@router.post("/restart")
def restart(
    request: Request,
    _session: Annotated[AuthSession, Depends(require_csrf)],
) -> JSONResponse:
    return _run(request, "restart")


@router.post("/apply-profile")
def apply_profile(
    body: ProfileOperationRequest,
    request: Request,
    _session: Annotated[AuthSession, Depends(require_csrf)],
) -> JSONResponse:
    return _run(request, "apply_profile", body.profile_id)
