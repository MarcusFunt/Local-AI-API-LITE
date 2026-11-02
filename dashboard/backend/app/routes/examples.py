from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import UploadFile

from app.auth import AuthSession, require_csrf, require_session
from app.example_proxy import (
    ALLOWED_AUDIO_TYPES,
    ChatTurnRequest,
    ExampleProxyError,
    SynthesizeRequest,
)
from app.example_runner import (
    ExampleActivityConflict,
    ExampleDependencyUnavailable,
    ExampleDockerUnavailable,
    ExampleRunnerError,
)

router = APIRouter(prefix="/api/control/v1/examples", tags=["examples"])


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _public(status) -> dict:
    if hasattr(status, "model_dump"):
        return status.model_dump(mode="json")
    if isinstance(status, dict):
        return status
    raise ValueError("Invalid example status")


def _proxy_error(error: ExampleProxyError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


@router.get("")
def list_examples(
    request: Request,
    _session: Annotated[AuthSession, Depends(require_session)],
) -> JSONResponse:
    try:
        statuses = request.app.state.example_runner.list_status()
        return JSONResponse(content={"examples": [_public(status) for status in statuses]})
    except Exception:  # noqa: BLE001 - never expose local runner or Docker details.
        raise HTTPException(status_code=503, detail="Example status is unavailable") from None


@router.post("/chat-voice/start")
def start_example(
    request: Request,
    _session: Annotated[AuthSession, Depends(require_csrf)],
    _body: EmptyRequest | None = None,
) -> JSONResponse:
    try:
        status = request.app.state.example_runner.start("chat-voice")
    except ExampleDependencyUnavailable:
        raise HTTPException(status_code=409, detail="The ddu-narrator model is not ready") from None
    except ExampleActivityConflict:
        raise HTTPException(status_code=409, detail="Another dashboard activity is active") from None
    except ExampleDockerUnavailable:
        raise HTTPException(
            status_code=503,
            detail="Docker is unavailable for the Chat + Voice example",
        ) from None
    except ExampleRunnerError:
        raise HTTPException(status_code=503, detail="The Chat + Voice example could not be started.") from None
    except ValueError:
        raise HTTPException(status_code=422, detail="The requested example is not supported") from None
    except Exception:  # noqa: BLE001 - never expose runner, subprocess, or local path details.
        raise HTTPException(status_code=503, detail="The Chat + Voice example could not be started.") from None
    public = _public(status)
    return JSONResponse(status_code=202 if public["state"] == "starting" else 200, content=public)


@router.post("/chat-voice/stop")
def stop_example(
    request: Request,
    _session: Annotated[AuthSession, Depends(require_csrf)],
    _body: EmptyRequest | None = None,
) -> JSONResponse:
    try:
        status = request.app.state.example_runner.stop("chat-voice")
    except ExampleActivityConflict:
        raise HTTPException(status_code=409, detail="Another dashboard activity is active") from None
    except ExampleDockerUnavailable:
        raise HTTPException(
            status_code=503,
            detail="Docker is unavailable for the Chat + Voice example",
        ) from None
    except ExampleRunnerError:
        raise HTTPException(status_code=503, detail="The Chat + Voice example could not be stopped.") from None
    except ValueError:
        raise HTTPException(status_code=422, detail="The requested example is not supported") from None
    except Exception:  # noqa: BLE001 - never expose runner, subprocess, or local path details.
        raise HTTPException(status_code=503, detail="The Chat + Voice example could not be stopped.") from None
    public = _public(status)
    return JSONResponse(status_code=202 if public["state"] != "stopped" else 200, content=public)


@router.post("/chat-voice/chat")
def chat_turn(
    request: Request,
    body: ChatTurnRequest,
    _session: Annotated[AuthSession, Depends(require_csrf)],
) -> JSONResponse:
    try:
        result = request.app.state.example_proxy.chat(body)
        return JSONResponse(content=result)
    except ExampleProxyError as exc:
        raise _proxy_error(exc) from None
    except Exception:  # noqa: BLE001 - upstream and local details never cross the proxy boundary.
        raise HTTPException(status_code=503, detail="The example service is unavailable") from None


@router.post("/chat-voice/transcribe")
async def transcribe_audio(
    request: Request,
    _session: Annotated[AuthSession, Depends(require_csrf)],
) -> JSONResponse:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise HTTPException(status_code=400, detail="Audio upload is invalid")
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 - multipart parser errors can contain submitted values.
        raise HTTPException(status_code=400, detail="Audio upload is invalid") from None
    items = list(form.multi_items())
    names = [name for name, _value in items]
    if names.count("language") == 0:
        await form.close()
        raise HTTPException(status_code=422, detail="Language is required")
    if len(items) != 2 or set(names) != {"file", "language"}:
        await form.close()
        raise HTTPException(status_code=400, detail="Audio upload is invalid")
    values = dict(items)
    upload = values.get("file")
    language = values.get("language")
    if not isinstance(upload, UploadFile):
        await form.close()
        raise HTTPException(status_code=400, detail="Audio upload is invalid")
    if not isinstance(language, str) or language not in {"da", "en"}:
        await form.close()
        raise HTTPException(status_code=422, detail="Language is not supported")
    try:
        content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
        if content_type not in ALLOWED_AUDIO_TYPES:
            raise HTTPException(status_code=415, detail="Audio format is not supported")
        audio = await upload.read(request.app.state.example_proxy.max_audio_bytes + 1)
        if len(audio) > request.app.state.example_proxy.max_audio_bytes:
            raise HTTPException(status_code=413, detail="Audio upload is too large")
        try:
            result = request.app.state.example_proxy.transcribe(audio, content_type, language)
            return JSONResponse(content=result)
        except ExampleProxyError as exc:
            raise _proxy_error(exc) from None
        except Exception:  # noqa: BLE001 - upstream and local details never cross the proxy boundary.
            raise HTTPException(status_code=503, detail="The speech service is unavailable") from None
    finally:
        await upload.close()


@router.post("/chat-voice/synthesize")
def synthesize_reply(
    request: Request,
    body: SynthesizeRequest,
    _session: Annotated[AuthSession, Depends(require_csrf)],
) -> Response:
    try:
        audio, content_type = request.app.state.example_proxy.synthesize(body)
        return Response(content=audio, media_type=content_type)
    except ExampleProxyError as exc:
        raise _proxy_error(exc) from None
    except Exception:  # noqa: BLE001 - upstream and local details never cross the proxy boundary.
        raise HTTPException(status_code=503, detail="The speech service is unavailable") from None


@router.api_route(
    "/chat-voice/{operation}",
    methods=["GET", "PUT", "PATCH", "DELETE", "OPTIONS", "POST"],
    include_in_schema=False,
)
def reject_unsupported_proxy_method(operation: str) -> None:
    if operation not in {"chat", "transcribe", "synthesize"}:
        raise HTTPException(status_code=404, detail="Not found")
    raise HTTPException(status_code=405, detail="Method not allowed")
