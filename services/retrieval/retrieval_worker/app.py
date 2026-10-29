from __future__ import annotations

import argparse
import asyncio
import math
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .contracts import (
    EXPECTED_DIMENSION,
    MAX_BODY_BYTES,
    MAX_EMBEDDING_BATCH_SIZE,
    MAX_INPUT_TOKENS,
    EmbeddingData,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingUsage,
    InputTooLongError,
    MissingArtifactError,
    QueueFullError,
    ServiceError,
)
from .models import EMBEDDING_ALIAS, EMBEDDING_METADATA
from .queue import InferenceQueue


class RequestBodyLimitMiddleware:
    """Bound request buffering before FastAPI parses JSON."""

    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        content_length = next(
            (
                value.decode("latin-1")
                for name, value in scope.get("headers", [])
                if name.lower() == b"content-length"
            ),
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
        except Exception:  # noqa: BLE001 - loader errors must map to a sanitized readiness state.
            self.status = "failed"
        else:
            self.adapter = adapter
            self.status = "ready"


def create_embedding_app(
    adapter_factory: Callable[[], Any] | None = None,
    *,
    inference_timeout_seconds: float = 60,
) -> FastAPI:
    if adapter_factory is None:
        from .embedding import create_embedding_adapter

        adapter_factory = create_embedding_adapter

    worker = WorkerState(adapter_factory)
    queue = InferenceQueue(max_outstanding=2, timeout_seconds=inference_timeout_seconds)

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
        title="Local AI API Lite retrieval worker",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=MAX_BODY_BYTES)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        malformed = any(error.get("type") == "json_invalid" for error in exc.errors())
        status_code = 400 if malformed else 422
        code = "malformed_json" if malformed else "invalid_request"
        message = "Malformed JSON." if malformed else "Request is invalid."
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": code, "message": message}},
        )

    @app.exception_handler(ServiceError)
    async def service_error_handler(_request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", response_model=None)
    async def ready(request: Request) -> Response | dict[str, str]:
        state: WorkerState = request.app.state.worker
        if state.status != "ready":
            return JSONResponse(status_code=503, content={"status": state.status})
        return {"status": "ready"}

    @app.get("/v1/models")
    async def models() -> dict[str, list[dict[str, object]]]:
        return {"data": [EMBEDDING_METADATA.as_dict()]}

    @app.post("/v1/embeddings", response_model=EmbeddingResponse)
    async def embeddings(payload: EmbeddingRequest, request: Request) -> EmbeddingResponse:
        if payload.model != EMBEDDING_ALIAS:
            raise ServiceError(422, "unsupported_model", "Model alias is not supported.")
        values = [payload.input] if isinstance(payload.input, str) else payload.input
        if not values or len(values) > MAX_EMBEDDING_BATCH_SIZE:
            raise ServiceError(422, "invalid_batch", "Input batch is outside supported limits.")
        if any(not value.strip() for value in values):
            raise ServiceError(422, "invalid_input", "Input strings must not be empty.")

        state: WorkerState = request.app.state.worker
        if state.status != "ready" or state.adapter is None:
            raise ServiceError(503, "not_ready", "Model is not ready.")
        adapter = state.adapter
        queue: InferenceQueue = request.app.state.inference_queue

        def count_and_embed() -> tuple[list[int], Any]:
            token_counts = [
                adapter.count_tokens(value, input_type=payload.input_type) for value in values
            ]
            if any(count > MAX_INPUT_TOKENS for count in token_counts):
                raise InputTooLongError()
            raw_vectors = adapter.embed(values, input_type=payload.input_type)
            return token_counts, raw_vectors

        try:
            token_counts, raw_vectors = await queue.run(count_and_embed)
        except InputTooLongError:
            raise ServiceError(422, "input_too_long", "Input exceeds the token limit.") from None
        except QueueFullError:
            raise ServiceError(429, "capacity", "Inference capacity is full.") from None
        except TimeoutError:
            raise ServiceError(504, "timeout", "Inference timed out.") from None
        except Exception:  # noqa: BLE001 - adapter errors must never reach HTTP responses.
            raise ServiceError(500, "inference_failed", "Inference failed.") from None

        try:
            vectors = _normalize_vectors(raw_vectors, expected_count=len(values))
        except (TypeError, ValueError, OverflowError):
            raise ServiceError(500, "invalid_model_output", "Inference returned invalid output.") from None
        data = [EmbeddingData(index=index, embedding=vector) for index, vector in enumerate(vectors)]
        total_tokens = sum(token_counts)
        return EmbeddingResponse(
            data=data,
            model=EMBEDDING_ALIAS,
            usage=EmbeddingUsage(prompt_tokens=total_tokens, total_tokens=total_tokens),
        )

    return app


def _normalize_vectors(raw_vectors: Any, *, expected_count: int) -> list[list[float]]:
    if len(raw_vectors) != expected_count:
        raise ValueError("vector count mismatch")
    normalized: list[list[float]] = []
    for raw_vector in raw_vectors:
        if len(raw_vector) != EXPECTED_DIMENSION:
            raise ValueError("vector dimension mismatch")
        vector = [float(value) for value in raw_vector]
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("vector contains non-finite values")
        norm = math.sqrt(sum(value * value for value in vector))
        if not math.isfinite(norm) or norm == 0:
            raise ValueError("vector norm is invalid")
        normalized.append([value / norm for value in vector])
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("embedding",), required=True)
    args = parser.parse_args()
    if args.role != "embedding":
        raise SystemExit("Unsupported worker role")
    import uvicorn

    uvicorn.run(create_embedding_app(), host="0.0.0.0", port=8080, access_log=False)


if __name__ == "__main__":
    main()
