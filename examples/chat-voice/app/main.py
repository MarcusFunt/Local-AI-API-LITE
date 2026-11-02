from __future__ import annotations

import hmac
import json
import os
import threading
from typing import cast

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import UploadFile

from .chat import ChatRequest, ChatService
from .errors import ServiceError
from .languages import SUPPORTED_LANGUAGES, Language
from .speech import ALLOWED_AUDIO_TYPES, MAX_AUDIO_BYTES, SpeechService

CHAT_BODY_LIMIT = 512_000
UPLOAD_OVERHEAD_LIMIT = 64_000
SYNTH_BODY_LIMIT = 64_000


class SynthesizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    language: Language
    text: str = Field(min_length=1, max_length=4000)


class RequestBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app
        self._capacity = threading.BoundedSemaphore(2)
        self._limits = {
            "/api/chat": CHAT_BODY_LIMIT,
            "/api/transcribe": MAX_AUDIO_BYTES + UPLOAD_OVERHEAD_LIMIT,
            "/api/synthesize": SYNTH_BODY_LIMIT,
        }

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        limit = self._limits.get(scope.get("path", ""))
        if limit is None:
            await self.app(scope, receive, send)
            return
        if not self._capacity.acquire(blocking=False):
            await self._reject(send, 429, "The example is busy; try again shortly")
            return
        try:
            await self._bounded_request(scope, receive, send, limit)
        finally:
            self._capacity.release()

    async def _bounded_request(self, scope, receive, send, limit: int):
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) < 0 or int(declared) > limit:
                    await self._reject(send, 413, "Request body is too large")
                    return
            except ValueError:
                await self._reject(send, 413, "Request body is too large")
                return

        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                await self._reject(send, 413, "Request body is too large")
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        sent = False

        async def replay():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)

    @staticmethod
    async def _reject(send, status_code: int, detail: str):
        body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def create_app(
    *,
    token: str | None = None,
    chat_service: object | None = None,
    speech_service: object | None = None,
) -> FastAPI:
    configured_token = os.environ.get("EXAMPLE_UPSTREAM_TOKEN", "") if token is None else token
    app = FastAPI(title="Chat + Voice consumer", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestBodyLimitMiddleware)
    app.state.upstream_token = configured_token
    app.state.chat_service = chat_service or ChatService()
    app.state.speech_service = speech_service or SpeechService()

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _error: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    def require_token(request: Request) -> None:
        expected = request.app.state.upstream_token
        supplied = request.headers.get("X-Example-Token", "")
        if not expected:
            raise HTTPException(status_code=503, detail="Example service is not configured")
        if not supplied or not hmac.compare_digest(expected, supplied):
            raise HTTPException(status_code=401, detail="Authentication required")

    def public_error(error: ServiceError) -> HTTPException:
        messages = {
            413: "Audio upload is too large",
            415: "Audio format is not supported",
            422: "The speech request is invalid",
            429: "The example is busy; try again shortly",
            502: "An upstream service returned an invalid response",
            503: "An upstream service is unavailable",
            504: "An upstream service timed out",
        }
        return HTTPException(status_code=error.status_code, detail=messages.get(error.status_code, "Request failed"))

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.post("/api/chat")
    def chat(body: ChatRequest, _authorized: None = Depends(require_token)):
        try:
            result = app.state.chat_service.complete(body)
            return result.as_dict() if hasattr(result, "as_dict") else result
        except ServiceError as exc:
            raise public_error(exc) from None
        except Exception:  # noqa: BLE001 - never return adapter or upstream details.
            raise HTTPException(status_code=503, detail="The model service is unavailable") from None

    @app.post("/api/transcribe")
    async def transcribe(request: Request, _authorized: None = Depends(require_token)):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
            raise HTTPException(status_code=400, detail="Audio upload is invalid")
        try:
            form = await request.form()
        except Exception:  # noqa: BLE001 - parser exceptions must not echo uploaded content.
            raise HTTPException(status_code=400, detail="Audio upload is invalid") from None
        try:
            items = list(form.multi_items())
            upload = form.get("file")
            language = form.get("language")
            if (
                len(items) != 2
                or {name for name, _value in items} != {"file", "language"}
                or not isinstance(upload, UploadFile)
                or not isinstance(language, str)
                or language not in SUPPORTED_LANGUAGES
            ):
                raise HTTPException(status_code=422, detail="Invalid request")
            content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
            if content_type not in ALLOWED_AUDIO_TYPES:
                raise HTTPException(status_code=415, detail="Audio format is not supported")
            audio = await upload.read(MAX_AUDIO_BYTES + 1)
            if len(audio) > MAX_AUDIO_BYTES:
                raise HTTPException(status_code=413, detail="Audio upload is too large")
            try:
                return app.state.speech_service.transcribe(audio, content_type, cast(Language, language))
            except ServiceError as exc:
                raise public_error(exc) from None
            except Exception:  # noqa: BLE001 - never return adapter or upstream details.
                raise HTTPException(status_code=503, detail="The speech service is unavailable") from None
        finally:
            await form.close()

    @app.post("/api/synthesize")
    def synthesize(body: SynthesizeRequest, _authorized: None = Depends(require_token)):
        try:
            audio, media_type = app.state.speech_service.synthesize(body.text, body.language)
            return Response(content=audio, media_type=media_type)
        except ServiceError as exc:
            raise public_error(exc) from None
        except Exception:  # noqa: BLE001 - never return adapter or upstream details.
            raise HTTPException(status_code=503, detail="The speech service is unavailable") from None

    return app


app = create_app()
