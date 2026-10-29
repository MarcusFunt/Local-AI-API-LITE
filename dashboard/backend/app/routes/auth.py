from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import (
    AuthenticationError,
    AuthService,
    AuthSession,
    require_csrf,
    require_session,
    validate_local_request,
)

SESSION_COOKIE = "dashboard_session"
router = APIRouter(prefix="/auth", tags=["authentication"])


class SetupRequest(BaseModel):
    token: str
    password: str


class LoginRequest(BaseModel):
    password: str


class RecoveryRequest(BaseModel):
    token: str
    new_password: str


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str


def _service(request: Request) -> AuthService:
    return request.app.state.auth_service


def _auth_failure():
    return HTTPException(status_code=401, detail="Authentication failed")


@router.get("/state")
def auth_state(request: Request) -> dict[str, bool]:
    service = _service(request)
    current = service.load_session(request.cookies.get(SESSION_COOKIE))
    return {
        "setup_required": service.is_setup_required(),
        "authenticated": current is not None,
    }


@router.post("/setup", status_code=204)
def setup(body: SetupRequest, request: Request) -> Response:
    validate_local_request(request)
    try:
        _service(request).setup(body.token, body.password)
    except AuthenticationError:
        raise _auth_failure() from None
    return Response(status_code=204)


@router.post("/login")
def login(body: LoginRequest, request: Request) -> Response:
    validate_local_request(request)
    client_key = request.client.host if request.client else "unknown"
    try:
        result = _service(request).login(body.password, client_key)
    except AuthenticationError:
        raise _auth_failure() from None

    response = JSONResponse(
        content={"csrf_token": result.csrf_token, "expires_at": result.session.expires_at}
    )
    response.set_cookie(
        key=SESSION_COOKIE,
        value=result.session_token,
        max_age=8 * 60 * 60,
        path="/",
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )
    return response


@router.post("/recovery", status_code=204)
def recovery(body: RecoveryRequest, request: Request) -> Response:
    validate_local_request(request)
    try:
        _service(request).recover_with_local_token(body.token, body.new_password)
    except AuthenticationError:
        raise _auth_failure() from None
    response = Response(status_code=204)
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    session: Annotated[AuthSession, Depends(require_csrf)],
) -> Response:
    _service(request).logout(session)
    response = Response(status_code=204)
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/password", status_code=204)
def change_password(
    body: PasswordChangeRequest,
    request: Request,
    session: Annotated[AuthSession, Depends(require_csrf)],
) -> Response:
    try:
        _service(request).change_password(session, body.old_password, body.new_password)
    except AuthenticationError:
        raise _auth_failure() from None
    response = Response(status_code=204)
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/sessions")
def sessions(
    request: Request,
    session: Annotated[AuthSession, Depends(require_session)],
) -> dict[str, object]:
    return {"sessions": _service(request).list_sessions(session)}


@router.post("/sessions/revoke-others")
def revoke_other_sessions(
    request: Request,
    session: Annotated[AuthSession, Depends(require_csrf)],
) -> dict[str, int]:
    count = _service(request).revoke_other_sessions(session)
    return {"revoked": count}
