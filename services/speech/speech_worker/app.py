from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .audio import InvalidAudioError, decode_audio_bounded
from .contracts import (
    INFERENCE_TIMEOUT_SECONDS,
    MAX_AUDIO_BYTES,
    MAX_AUDIO_SECONDS,
    MAX_OUTSTANDING_REQUESTS,
    MAX_STT_REQUEST_BYTES,
    MAX_TTS_REQUEST_BYTES,
    STT_ALIAS,
    TTS_ALIAS,
    AudioTooLongError,
    MissingArtifactError,
    QueueFullError,
    SpeechError,
    SpeechLanguage,
    SpeechRequest,
)
from .models import STT_METADATA, TTS_METADATA, create_stt_adapter, create_tts_adapter
from .queue import InferenceQueue


class RequestBodyLimitMiddleware:
    """Bound request buffering before multipart or JSON parsing starts."""

    def __init__(self, app: ASGIApp, *, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        content_length = next(
            (value.decode("latin-1") for name, value in scope.get("headers", []) if name.lower() == b"content-length"),
            None,
        )
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await self._too_large(scope, receive, send)
                    return
            except ValueError:
                await self._too_large(scope, receive, send)
                return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                await self.app(scope, receive, send)
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                await self._too_large(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        body_bytes = bytes(body)
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        await self.app(scope, replay, send)

    @staticmethod
    async def _too_large(scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse(
            status_code=413,
            content={"error": {"code": "payload_too_large", "message": "Request body is too large."}},
        )(scope, receive, send)


class WorkerState:
    def __init__(self, adapter_factory: Callable[[], Any]):
        self.adapter_factory = adapter_factory
        self.status = "loading"
        self.adapter: Any | None = None

    async def initialize(self) -> None:
        try:
            adapter = await asyncio.to_thread(self.adapter_factory)
        except MissingArtifactError:
            self.status = "missing_artifact"
        except Exception:  # noqa: BLE001 - never expose cache paths, decoder data or runtime exceptions.
            self.status = "failed"
        else:
            self.adapter = adapter
            self.status = "ready"


def _create_worker_app(
    adapter_factory: Callable[[], Any],
    metadata: dict[str, Any],
    *,
    request_limit: int,
    inference_timeout_seconds: float,
) -> FastAPI:
    worker = WorkerState(adapter_factory)
    queue = InferenceQueue(
        max_outstanding=MAX_OUTSTANDING_REQUESTS,
        timeout_seconds=inference_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.worker = worker
        app.state.inference_queue = queue
        initializer = asyncio.create_task(worker.initialize())
        try:
            yield
        finally:
            initializer.cancel()
            try:
                await initializer
            except asyncio.CancelledError:
                pass
            await queue.close()

    app = FastAPI(
        title="Local AI API Lite speech worker",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=request_limit)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        malformed = any(error.get("type") == "json_invalid" for error in exc.errors())
        status_code = 400 if malformed else 422
        code = "malformed_request" if malformed else "invalid_request"
        message = "Malformed request." if malformed else "Request is invalid."
        return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})

    @app.exception_handler(SpeechError)
    async def speech_error_handler(_request: Request, exc: SpeechError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", response_model=None)
    async def ready(request: Request) -> Response | dict[str, str]:
        current: WorkerState = request.app.state.worker
        if current.status != "ready":
            return JSONResponse(status_code=503, content={"status": current.status})
        queue_state = request.app.state.inference_queue.snapshot()
        if queue_state["stuck_inference"] or queue_state["saturated"]:
            return JSONResponse(
                status_code=503,
                content={"status": "degraded", "reason_category": "inference_capacity"},
            )
        return {"status": "ready"}

    @app.get("/v1/models")
    async def models() -> dict[str, list[dict[str, Any]]]:
        return {"data": [metadata]}

    @app.get("/metrics")
    async def metrics(request: Request) -> dict[str, int | bool | str | None]:
        return request.app.state.inference_queue.snapshot()

    app.state.worker = worker
    app.state.inference_queue = queue
    return app


def create_stt_app(
    adapter_factory: Callable[[], Any] | None = None,
    *,
    audio_decoder: Callable[[Any, int], tuple[Any, float]] | None = None,
    inference_timeout_seconds: float = INFERENCE_TIMEOUT_SECONDS,
) -> FastAPI:
    adapter_factory = adapter_factory or create_stt_adapter
    audio_decoder = audio_decoder or decode_audio_bounded
    app = _create_worker_app(
        adapter_factory,
        STT_METADATA,
        request_limit=MAX_STT_REQUEST_BYTES,
        inference_timeout_seconds=inference_timeout_seconds,
    )

    @app.post("/v1/audio/transcriptions", response_model=None)
    async def transcriptions(
        request: Request,
        file: UploadFile = File(...),  # noqa: B008 - FastAPI uses this to declare multipart input.
        model: str = Form(STT_ALIAS),
        language: SpeechLanguage = Form("da"),
        response_format: Literal["json", "verbose_json", "text"] = Form("json"),
    ) -> Response | dict[str, Any]:
        if model != STT_ALIAS:
            raise SpeechError(422, "unsupported_model", "Model alias is not supported.")
        audio_size = file.size
        if audio_size is None:
            file.file.seek(0, 2)
            audio_size = file.file.tell()
            file.file.seek(0)
        if audio_size <= 0:
            raise SpeechError(400, "invalid_audio", "Audio file is empty or invalid.")
        if audio_size > MAX_AUDIO_BYTES:
            raise SpeechError(413, "audio_size_limit", "Audio file exceeds the size limit.")
        state: WorkerState = request.app.state.worker
        if state.status != "ready" or state.adapter is None:
            raise SpeechError(503, "not_ready", "Speech model is not ready.")

        def transcribe() -> dict[str, Any]:
            try:
                audio, duration = audio_decoder(file.file, MAX_AUDIO_SECONDS)
            except AudioTooLongError:
                raise SpeechError(413, "audio_duration_limit", "Audio exceeds the duration limit.") from None
            except InvalidAudioError:
                raise SpeechError(400, "invalid_audio", "Audio file is empty or invalid.") from None
            result = state.adapter.transcribe(audio, language=language)
            if not isinstance(result, dict) or not isinstance(result.get("text"), str):
                raise SpeechError(500, "invalid_model_output", "Speech model returned invalid output.")
            if len(result["text"]) > 32_000:
                raise SpeechError(500, "invalid_model_output", "Speech model returned invalid output.")
            result.setdefault("language", language)
            result.setdefault("duration", duration)
            result.setdefault("segments", [])
            return result

        try:
            result = await request.app.state.inference_queue.run(transcribe)
        except SpeechError:
            raise
        except QueueFullError:
            raise SpeechError(429, "capacity", "Speech service capacity is full.") from None
        except TimeoutError:
            raise SpeechError(504, "timeout", "Speech inference timed out.") from None
        except Exception:  # noqa: BLE001 - transcription and decoder errors may include input content.
            raise SpeechError(500, "inference_failed", "Speech inference failed.") from None
        if response_format == "text":
            return PlainTextResponse(result["text"])
        if response_format == "json":
            return {"text": result["text"]}
        return {
            "text": result["text"],
            "language": result["language"],
            "duration": result["duration"],
            "segments": result["segments"],
        }

    return app


def create_tts_app(
    adapter_factory: Callable[[], Any] | None = None,
    *,
    inference_timeout_seconds: float = INFERENCE_TIMEOUT_SECONDS,
) -> FastAPI:
    adapter_factory = adapter_factory or create_tts_adapter
    app = _create_worker_app(
        adapter_factory,
        TTS_METADATA,
        request_limit=MAX_TTS_REQUEST_BYTES,
        inference_timeout_seconds=inference_timeout_seconds,
    )

    @app.post("/v1/audio/speech")
    async def speech(payload: SpeechRequest, request: Request) -> Response:
        if payload.model != TTS_ALIAS:
            raise SpeechError(422, "unsupported_model", "Model alias is not supported.")
        if not payload.input.strip():
            raise SpeechError(422, "invalid_input", "Input text must not be empty.")
        state: WorkerState = request.app.state.worker
        if state.status != "ready" or state.adapter is None:
            raise SpeechError(503, "not_ready", "Speech model is not ready.")

        try:
            wav_bytes = await request.app.state.inference_queue.run(
                lambda: state.adapter.synthesize_wav(payload.input, language=payload.language)
            )
        except QueueFullError:
            raise SpeechError(429, "capacity", "Speech service capacity is full.") from None
        except TimeoutError:
            raise SpeechError(504, "timeout", "Speech inference timed out.") from None
        except Exception:  # noqa: BLE001 - synthesis errors may contain text or local voice paths.
            raise SpeechError(500, "inference_failed", "Speech synthesis failed.") from None
        if not isinstance(wav_bytes, bytes) or not wav_bytes.startswith(b"RIFF") or b"WAVE" not in wav_bytes[:16]:
            raise SpeechError(500, "invalid_model_output", "Speech model returned invalid audio.")
        return Response(content=wav_bytes, media_type="audio/wav")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the fixed local STT or TTS worker.")
    parser.add_argument("--role", choices=("stt", "tts"), required=True)
    args = parser.parse_args()
    import uvicorn

    application = create_stt_app() if args.role == "stt" else create_tts_app()
    uvicorn.run(application, host="0.0.0.0", port=8080, access_log=False)


if __name__ == "__main__":
    main()
