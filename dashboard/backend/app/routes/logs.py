from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.auth import require_session
from app.logs import MAX_LOG_TAIL

router = APIRouter(prefix="/api/control/v1/logs", tags=["logs"])


@router.get("", dependencies=[Depends(require_session)])
def read_logs(
    request: Request,
    tail: Annotated[int, Query(ge=1, le=MAX_LOG_TAIL)] = 100,
) -> dict[str, Any]:
    return request.app.state.log_reader.read(tail=tail)
