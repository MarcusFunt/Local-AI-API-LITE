from __future__ import annotations

import asyncio
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[1] / "services" / "retrieval"
sys.path.insert(0, str(SERVICE_ROOT))

from retrieval_worker.app import create_embedding_app
from retrieval_worker.contracts import MAX_BODY_BYTES, MissingArtifactError
from retrieval_worker.embedding import format_embedding_input
from retrieval_worker.install_models import main as install_models_main
from retrieval_worker.queue import InferenceQueue, QueueFullError


class FakeEmbeddingAdapter:
    def __init__(self, *, token_count: int = 2, vector: list[float] | None = None):
        self.token_count = token_count
        self.vector = vector
        self.counted: list[tuple[str, str]] = []
        self.embedded: list[tuple[list[str], str]] = []

    def count_tokens(self, text: str, *, input_type: str) -> int:
        self.counted.append((text, input_type))
        return self.token_count

    def embed(self, inputs: list[str], *, input_type: str) -> list[list[float]]:
        self.embedded.append((inputs, input_type))
        if self.vector is not None:
            return [self.vector for _ in inputs]
        vectors = []
        for index, _ in enumerate(inputs):
            vector = [0.0] * 1024
            vector[index] = 1.0
            vectors.append(vector)
        return vectors


def wait_until_ready(client: TestClient) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if client.get("/ready").status_code == 200:
            return
        time.sleep(0.01)
    pytest.fail("fake adapter did not become ready")


def test_embedding_response_preserves_order_and_returns_normalized_1024_vectors():
    adapter = FakeEmbeddingAdapter()
    app = create_embedding_app(adapter_factory=lambda: adapter)

    with TestClient(app) as client:
        wait_until_ready(client)
        response = client.post(
            "/v1/embeddings",
            json={
                "model": "ddu-embed",
                "input": ["first passage", "second passage"],
                "input_type": "document",
                "encoding_format": "float",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    assert payload["model"] == "ddu-embed"
    assert [item["index"] for item in payload["data"]] == [0, 1]
    first, second = [item["embedding"] for item in payload["data"]]
    assert len(first) == len(second) == 1024
    assert first[0] == 1.0 and second[1] == 1.0
    assert sum(value * value for value in first) == pytest.approx(1.0)
    assert payload["usage"] == {"prompt_tokens": 4, "total_tokens": 4}
    assert adapter.embedded == [(["first passage", "second passage"], "document")]


def test_query_and_document_mode_is_required_and_query_mode_is_forwarded():
    adapter = FakeEmbeddingAdapter()
    app = create_embedding_app(adapter_factory=lambda: adapter)

    with TestClient(app) as client:
        wait_until_ready(client)
        missing_mode = client.post(
            "/v1/embeddings", json={"model": "ddu-embed", "input": "secret query"}
        )
        query = client.post(
            "/v1/embeddings",
            json={
                "model": "ddu-embed",
                "input": "secret query",
                "input_type": "query",
            },
        )

    assert missing_mode.status_code == 422
    assert "secret query" not in missing_mode.text
    assert query.status_code == 200
    assert adapter.embedded == [(["secret query"], "query")]


def test_qwen_query_instruction_is_versioned_and_documents_receive_no_instruction():
    query = "Hvorfor ønskede Frankrig hårdere fredsbetingelser?"

    assert format_embedding_input(query, input_type="query") == (
        "Instruct: Given a question, retrieve relevant passages that answer the question\n"
        f"Query: {query}"
    )
    assert format_embedding_input(query, input_type="document") == query


def test_validation_error_does_not_echo_request_text():
    adapter = FakeEmbeddingAdapter()
    app = create_embedding_app(adapter_factory=lambda: adapter)
    sentinel = "DO_NOT_ECHO_very_private_passage_8291"

    with TestClient(app) as client:
        wait_until_ready(client)
        response = client.post(
            "/v1/embeddings",
            json={
                "model": "ddu-embed",
                "input": [sentinel],
                "input_type": "unsupported-mode",
            },
        )

    assert response.status_code == 422
    assert sentinel not in response.text
    assert adapter.embedded == []


def test_malformed_json_is_a_sanitized_400():
    adapter = FakeEmbeddingAdapter()
    app = create_embedding_app(adapter_factory=lambda: adapter)
    sentinel = "SECRET_TEXT_IN_INVALID_JSON"

    with TestClient(app) as client:
        wait_until_ready(client)
        response = client.post(
            "/v1/embeddings",
            content=f'{{"input":"{sentinel}"',
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 400
    assert sentinel not in response.text
    assert response.json()["error"]["code"] == "malformed_json"


def test_unsupported_model_alias_is_rejected_without_echoing_the_model_value():
    adapter = FakeEmbeddingAdapter()
    app = create_embedding_app(adapter_factory=lambda: adapter)
    supplied_model = "C:/private/path/model-name"

    with TestClient(app) as client:
        wait_until_ready(client)
        response = client.post(
            "/v1/embeddings",
            json={"model": supplied_model, "input": "question", "input_type": "query"},
        )

    assert response.status_code == 422
    assert supplied_model not in response.text
    assert adapter.embedded == []


def test_batch_over_limit_is_rejected_before_embedding():
    adapter = FakeEmbeddingAdapter()
    app = create_embedding_app(adapter_factory=lambda: adapter)

    with TestClient(app) as client:
        wait_until_ready(client)
        response = client.post(
            "/v1/embeddings",
            json={
                "model": "ddu-embed",
                "input": [f"passage {index}" for index in range(9)],
                "input_type": "document",
            },
        )

    assert response.status_code == 422
    assert adapter.embedded == []


def test_oversized_token_input_is_rejected_without_adapter_call():
    adapter = FakeEmbeddingAdapter(token_count=2049)
    app = create_embedding_app(adapter_factory=lambda: adapter)

    with TestClient(app) as client:
        wait_until_ready(client)
        response = client.post(
            "/v1/embeddings",
            json={"model": "ddu-embed", "input": "too many tokens", "input_type": "query"},
        )

    assert response.status_code == 422
    assert adapter.counted
    assert adapter.embedded == []


def test_oversized_input_is_rejected_without_adapter_call():
    adapter = FakeEmbeddingAdapter()
    app = create_embedding_app(adapter_factory=lambda: adapter)
    raw_body = b"{" + b" " * MAX_BODY_BYTES + b"}"

    with TestClient(app) as client:
        wait_until_ready(client)
        response = client.post(
            "/v1/embeddings",
            content=raw_body,
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert adapter.embedded == []


def test_non_finite_model_output_is_sanitized():
    vector = [0.0] * 1024
    vector[0] = float("nan")
    adapter = FakeEmbeddingAdapter(vector=vector)
    app = create_embedding_app(adapter_factory=lambda: adapter)

    with TestClient(app) as client:
        wait_until_ready(client)
        response = client.post(
            "/v1/embeddings",
            json={"model": "ddu-embed", "input": "question", "input_type": "query"},
        )

    assert response.status_code == 500
    assert "nan" not in response.text.lower()
    assert "traceback" not in response.text.lower()
    assert "embedding" not in response.json()


def test_missing_artifact_is_not_ready_but_health_is_live():
    def missing_model():
        raise MissingArtifactError()

    app = create_embedding_app(adapter_factory=missing_model)

    with TestClient(app) as client:
        health = client.get("/health")
        deadline = time.monotonic() + 2
        ready = client.get("/ready")
        while ready.json().get("status") == "loading" and time.monotonic() < deadline:
            time.sleep(0.01)
            ready = client.get("/ready")

    assert health.status_code == 200
    assert ready.status_code == 503
    assert ready.json() == {"status": "missing_artifact"}
    assert "path" not in ready.text.lower()


def test_model_metadata_is_stable_and_does_not_expose_local_paths():
    app = create_embedding_app(adapter_factory=FakeEmbeddingAdapter)

    with TestClient(app) as client:
        response = client.get("/v1/models")

    model = response.json()["data"][0]
    assert model["id"] == "ddu-embed"
    assert model["task"] == "embedding"
    assert model["revision"] == "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
    assert model["capabilities"]["dimensions"] == 1024
    assert "path" not in response.text.lower()


def test_no_interactive_api_documentation_routes_are_exposed():
    app = create_embedding_app(adapter_factory=FakeEmbeddingAdapter)

    with TestClient(app) as client:
        for route in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(route).status_code == 404


def test_installer_plan_is_read_only_and_only_lists_the_fixed_snapshot(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["install_models", "--plan"])

    install_models_main()

    output = capsys.readouterr().out
    assert "ddu-embed" in output
    assert "Qwen/Qwen3-Embedding-0.6B@97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3" in output
    assert "Plan only; no files were written." in output


def test_malformed_installer_invocation_does_not_start_a_download(monkeypatch):
    monkeypatch.setattr("sys.argv", ["install_models"])

    with pytest.raises(SystemExit) as error:
        install_models_main()

    assert error.value.code == 2


def test_installer_requires_explicit_opt_in_and_uses_only_the_pinned_snapshot(
    monkeypatch, capsys
):
    calls: list[dict[str, object]] = []
    fake_hub = types.ModuleType("huggingface_hub")
    fake_hub.snapshot_download = lambda **kwargs: calls.append(kwargs)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    monkeypatch.setattr("sys.argv", ["install_models", "--install"])

    install_models_main()

    assert calls == [
        {
            "repo_id": "Qwen/Qwen3-Embedding-0.6B",
            "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
            "cache_dir": "/models/hub",
            "local_files_only": False,
        }
    ]
    assert "Pinned embedding artifact installed." in capsys.readouterr().out


def test_inference_error_does_not_echo_adapter_exception():
    class BrokenAdapter(FakeEmbeddingAdapter):
        def embed(self, inputs: list[str], *, input_type: str) -> list[list[float]]:
            raise RuntimeError("PRIVATE_TEXT_AND_LOCAL_PATH")

    app = create_embedding_app(adapter_factory=BrokenAdapter)

    with TestClient(app) as client:
        wait_until_ready(client)
        response = client.post(
            "/v1/embeddings",
            json={"model": "ddu-embed", "input": "question", "input_type": "query"},
        )

    assert response.status_code == 500
    assert "PRIVATE_TEXT_AND_LOCAL_PATH" not in response.text
    assert "traceback" not in response.text.lower()


def test_inference_timeout_maps_to_504_and_does_not_release_active_thread_early():
    started = threading.Event()
    release = threading.Event()

    class SlowAdapter(FakeEmbeddingAdapter):
        def embed(self, inputs: list[str], *, input_type: str) -> list[list[float]]:
            started.set()
            release.wait(timeout=3)
            return super().embed(inputs, input_type=input_type)

    app = create_embedding_app(
        adapter_factory=SlowAdapter,
        inference_timeout_seconds=0.02,
    )

    with TestClient(app) as client:
        wait_until_ready(client)
        response = client.post(
            "/v1/embeddings",
            json={"model": "ddu-embed", "input": "question", "input_type": "query"},
        )
        assert started.is_set()
        assert app.state.inference_queue.outstanding == 1
        release.set()

    assert response.status_code == 504


def test_third_outstanding_request_is_rejected():
    started = threading.Event()
    release = threading.Event()

    class BlockingAdapter(FakeEmbeddingAdapter):
        def embed(self, inputs: list[str], *, input_type: str) -> list[list[float]]:
            started.set()
            release.wait(timeout=3)
            return super().embed(inputs, input_type=input_type)

    adapter = BlockingAdapter()
    app = create_embedding_app(adapter_factory=lambda: adapter)
    body = {"model": "ddu-embed", "input": "question", "input_type": "query"}

    with TestClient(app) as client:
        wait_until_ready(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.post, "/v1/embeddings", json=body)
            assert started.wait(timeout=2)
            second = pool.submit(client.post, "/v1/embeddings", json=body)
            deadline = time.monotonic() + 2
            while app.state.inference_queue.outstanding < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert app.state.inference_queue.outstanding == 2
            third = client.post("/v1/embeddings", json=body)
            release.set()
            assert first.result(timeout=3).status_code == 200
            assert second.result(timeout=3).status_code == 200

    assert third.status_code == 429


def test_timed_out_inference_keeps_capacity_until_worker_finishes():
    started = threading.Event()
    release = threading.Event()

    def blocking_call() -> str:
        started.set()
        release.wait(timeout=3)
        return "done"

    async def scenario() -> None:
        queue = InferenceQueue(max_outstanding=2, timeout_seconds=0.02)
        try:
            with pytest.raises(TimeoutError):
                await queue.run(blocking_call)
            assert started.is_set()
            assert queue.outstanding == 1
            release.set()
            deadline = time.monotonic() + 2
            while queue.outstanding and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert queue.outstanding == 0
        finally:
            release.set()
            await queue.close()

    asyncio.run(scenario())


def test_queue_rejects_capacity_without_starting_third_call():
    started = threading.Event()
    release = threading.Event()
    invoked: list[str] = []

    def active() -> str:
        invoked.append("active")
        started.set()
        release.wait(timeout=3)
        return "active"

    def queued() -> str:
        invoked.append("queued")
        return "queued"

    async def scenario() -> None:
        queue = InferenceQueue(max_outstanding=2, timeout_seconds=2)
        try:
            first = asyncio.create_task(queue.run(active))
            assert await asyncio.to_thread(started.wait, 2)
            second = asyncio.create_task(queue.run(queued))
            deadline = time.monotonic() + 2
            while queue.outstanding < 2 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            with pytest.raises(QueueFullError):
                await queue.run(lambda: invoked.append("rejected"))
            release.set()
            assert await first == "active"
            assert await second == "queued"
        finally:
            release.set()
            await queue.close()

    asyncio.run(scenario())
    assert invoked == ["active", "queued"]
