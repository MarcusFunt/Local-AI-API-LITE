#!/usr/bin/env python3
"""Exercise the live speech, retrieval, reranking, and chat models as one chain.

Run from a trusted client attached to ``ddu_inference_bus``. The runner only calls
model APIs; it never starts, stops, or reconfigures services. It prints timings,
model identities, and output sizes, but never prints prompt, transcript, answer,
passage, or audio content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "scripts" / "fixtures" / "model_chain_v1.json"
SERVICE_ORDER = ("narrator", "embed", "rerank", "stt", "tts")
SERVICE_PINS = {
    "narrator": {
        "alias": "ddu-narrator",
        "revision": "7a613872f394578b0b52b683ff4ac47516b4bcaf",
        "revision_reported_by_api": False,
        "health_path": "/health",
        "ready_path": None,
        "url_env": "MODEL_CHAIN_NARRATOR_URL",
        "url_default": "http://ddu-llm:8080",
    },
    "embed": {
        "alias": "ddu-embed",
        "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        "revision_reported_by_api": True,
        "health_path": "/health",
        "ready_path": "/ready",
        "url_env": "MODEL_CHAIN_EMBED_URL",
        "url_default": "http://ddu-embed:8080",
    },
    "rerank": {
        "alias": "ddu-rerank",
        "revision": "8215cf04918ba6f7b6a62bb44238ce2953d8831c",
        "revision_reported_by_api": True,
        "health_path": "/health",
        "ready_path": "/ready",
        "url_env": "MODEL_CHAIN_RERANK_URL",
        "url_default": "http://ddu-rerank:8080",
    },
    "stt": {
        "alias": "ddu-stt",
        "revision": "536b0662742c02347bc0e980a01041f333bce120",
        "revision_reported_by_api": True,
        "health_path": "/health",
        "ready_path": "/ready",
        "url_env": "MODEL_CHAIN_STT_URL",
        "url_default": "http://ddu-stt:8080",
    },
    "tts": {
        "alias": "ddu-tts",
        "revision": "320d5f7f7751a17ef6512d5c23863056c6a11c0f",
        "revision_reported_by_api": True,
        "health_path": "/health",
        "ready_path": "/ready",
        "url_env": "MODEL_CHAIN_TTS_URL",
        "url_default": "http://ddu-tts:8080",
    },
}
MAX_JSON_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_AUDIO_RESPONSE_BYTES = 25_000_000
MAX_RERANK_PAIRS = 20
MAX_EVIDENCE_PASSAGES = 5


class ModelChainError(RuntimeError):
    """A sanitized model-chain failure category."""

    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


def _request_bytes(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    content_type: str | None = None,
    timeout: float,
    max_response_bytes: int = MAX_JSON_RESPONSE_BYTES,
) -> tuple[bytes, dict[str, str]]:
    headers = {"Accept": "application/json, audio/wav"}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read(max_response_bytes + 1)
            response_headers = {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        raise ModelChainError(f"http_{exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ModelChainError("service_unavailable_or_timeout") from None
    if len(response_body) > max_response_bytes:
        raise ModelChainError("response_size_limit")
    return response_body, response_headers


def _json_request(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    body = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if payload is not None
        else None
    )
    response, _headers = _request_bytes(
        method,
        url,
        body=body,
        content_type="application/json" if body is not None else None,
        timeout=timeout,
    )
    try:
        parsed = json.loads(response)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ModelChainError("invalid_json_response") from None
    if not isinstance(parsed, dict):
        raise ModelChainError("invalid_response_shape")
    return parsed


def _validate_wav(audio: bytes) -> None:
    if not isinstance(audio, bytes) or len(audio) < 16 or not audio.startswith(b"RIFF") or b"WAVE" not in audio[:16]:
        raise ModelChainError("invalid_wav_output")


class ModelClient:
    """Small OpenAI-compatible client for the five private inference services."""

    def __init__(self, *, timeout_seconds: float = 300, urls: dict[str, str] | None = None):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        self.urls = {
            name: (urls or {}).get(name)
            or os.environ.get(spec["url_env"], spec["url_default"])
            for name, spec in SERVICE_PINS.items()
        }

    def _json(self, service: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return _json_request(
            method,
            f"{self.urls[service].rstrip('/')}{path}",
            payload=payload,
            timeout=self.timeout_seconds,
        )

    def verify_services(self) -> dict[str, dict[str, Any]]:
        identities: dict[str, dict[str, Any]] = {}
        for name in SERVICE_ORDER:
            spec = SERVICE_PINS[name]
            base_url = self.urls[name].rstrip("/")
            _request_bytes(
                "GET",
                f"{base_url}{spec['health_path']}",
                timeout=min(self.timeout_seconds, 10),
            )
            if spec["ready_path"]:
                ready = self._json(name, "GET", spec["ready_path"])
                if ready.get("status") != "ready":
                    raise ModelChainError(f"{name}_not_ready")
            models = self._json(name, "GET", "/v1/models").get("data")
            if not isinstance(models, list):
                raise ModelChainError(f"{name}_model_identity_unavailable")
            model = next(
                (item for item in models if isinstance(item, dict) and item.get("id") == spec["alias"]),
                None,
            )
            if model is None:
                raise ModelChainError(f"{name}_model_alias_mismatch")
            reported_revision = model.get("revision")
            if spec["revision_reported_by_api"] and reported_revision != spec["revision"]:
                raise ModelChainError(f"{name}_revision_mismatch")
            if isinstance(reported_revision, str) and reported_revision != spec["revision"]:
                raise ModelChainError(f"{name}_revision_mismatch")
            identities[name] = {
                "alias": spec["alias"],
                "expected_revision": spec["revision"],
                "reported_revision": reported_revision if isinstance(reported_revision, str) else None,
                "revision_verified": reported_revision == spec["revision"],
                "ready": True,
            }
        return identities

    def synthesize(self, text: str) -> bytes:
        response, headers = _request_bytes(
            "POST",
            f"{self.urls['tts'].rstrip('/')}/v1/audio/speech",
            body=json.dumps(
                {
                    "model": "ddu-tts",
                    "input": text,
                    "voice": "da_DK-talesyntese-medium",
                    "response_format": "wav",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
            content_type="application/json",
            timeout=self.timeout_seconds,
            max_response_bytes=MAX_AUDIO_RESPONSE_BYTES,
        )
        if not headers.get("content-type", "").startswith("audio/wav"):
            raise ModelChainError("invalid_wav_content_type")
        _validate_wav(response)
        return response

    def transcribe(self, audio: bytes) -> str:
        boundary = f"----model-chain-{uuid.uuid4().hex}"
        parts: list[bytes] = []
        for name, value in (
            ("model", "ddu-stt"),
            ("language", "da"),
            ("response_format", "json"),
        ):
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
            )
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"synthetic.wav\"\r\n"
            "Content-Type: audio/wav\r\n\r\n".encode()
            + audio
            + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode())
        response, _headers = _request_bytes(
            "POST",
            f"{self.urls['stt'].rstrip('/')}/v1/audio/transcriptions",
            body=b"".join(parts),
            content_type=f"multipart/form-data; boundary={boundary}",
            timeout=self.timeout_seconds,
        )
        try:
            result = json.loads(response)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ModelChainError("invalid_transcription_response") from None
        text = result.get("text") if isinstance(result, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ModelChainError("empty_transcription")
        return text.strip()

    def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        if input_type not in {"query", "document"} or not texts:
            raise ModelChainError("invalid_embedding_request")
        response = self._json(
            "embed",
            "POST",
            "/v1/embeddings",
            {"model": "ddu-embed", "input": texts, "input_type": input_type},
        )
        records = response.get("data")
        if not isinstance(records, list) or len(records) != len(texts):
            raise ModelChainError("invalid_embedding_response")
        vectors: list[list[float]] = []
        for record in records:
            vector = record.get("embedding") if isinstance(record, dict) else None
            if (
                not isinstance(vector, list)
                or not vector
                or any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in vector)
            ):
                raise ModelChainError("invalid_embedding_response")
            vectors.append([float(value) for value in vector])
        return vectors

    def rerank(self, query: str, documents: list[str]) -> list[int]:
        response = self._json(
            "rerank",
            "POST",
            "/v1/rerank",
            {"model": "ddu-rerank", "query": query, "documents": documents, "top_n": len(documents)},
        )
        results = response.get("results")
        if not isinstance(results, list) or len(results) != len(documents):
            raise ModelChainError("invalid_reranking_response")
        indexes: list[int] = []
        for result in results:
            index = result.get("index") if isinstance(result, dict) else None
            score = result.get("relevance_score") if isinstance(result, dict) else None
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not math.isfinite(score)
            ):
                raise ModelChainError("invalid_reranking_response")
            indexes.append(index)
        if sorted(indexes) != list(range(len(documents))):
            raise ModelChainError("invalid_reranking_response")
        return indexes

    def generate(self, query: str, passages: list[dict[str, str]]) -> str:
        evidence = "\n".join(f"[{item['id']}] {item['text']}" for item in passages)
        response = self._json(
            "narrator",
            "POST",
            "/v1/chat/completions",
            {
                "model": "ddu-narrator",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Answer using only the explicitly fictional test passages. "
                            "Reply in Danish with one short sentence and cite the passage ID in brackets. "
                            "If the passages do not answer the question, say so."
                        ),
                    },
                    {"role": "user", "content": f"Question: {query}\nFictional passages:\n{evidence}"},
                ],
                "temperature": 0,
                "max_tokens": 96,
                "seed": 42,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        choices = response.get("choices")
        try:
            answer = choices[0]["message"]["content"]
        except (IndexError, KeyError, TypeError):
            raise ModelChainError("invalid_generation_response") from None
        if not isinstance(answer, str) or not answer.strip():
            raise ModelChainError("empty_generation")
        return answer.strip()


def rank_passages(
    query_vector: list[float],
    document_vectors: list[list[float]],
    passages: list[dict[str, str]],
) -> list[dict[str, str]]:
    if not isinstance(query_vector, list) or not query_vector or len(document_vectors) != len(passages):
        raise ModelChainError("embedding_response_invalid")
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("text"), str) for item in passages):
        raise ModelChainError("fixture_invalid")
    if any(len(vector) != len(query_vector) for vector in document_vectors):
        raise ModelChainError("embedding_response_invalid")
    vectors = [query_vector, *document_vectors]
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        for vector in vectors
        for value in vector
    ):
        raise ModelChainError("embedding_response_invalid")
    scores = [
        sum(float(left) * float(right) for left, right in zip(query_vector, vector, strict=True))
        for vector in document_vectors
    ]
    return [dict(passages[index]) for index in sorted(range(len(passages)), key=lambda index: (-scores[index], index))]


def _measure(timings: dict[str, float], name: str, operation: Any) -> Any:
    started = time.perf_counter()
    try:
        return operation()
    finally:
        timings[name] = round((time.perf_counter() - started) * 1000, 3)


def run_model_chain(
    client: Any,
    *,
    question: str,
    passages: list[dict[str, str]],
    evidence_count: int = 3,
) -> dict[str, Any]:
    if not isinstance(question, str) or not question.strip():
        raise ModelChainError("fixture_invalid")
    if not passages or len(passages) > MAX_RERANK_PAIRS:
        raise ModelChainError("fixture_invalid")
    if not 1 <= evidence_count <= MAX_EVIDENCE_PASSAGES:
        raise ModelChainError("evidence_count_invalid")
    timings: dict[str, float] = {}
    services = _measure(timings, "service_verification", client.verify_services)
    input_audio = _measure(timings, "question_synthesis", lambda: client.synthesize(question))
    _validate_wav(input_audio)
    recognized_question = _measure(timings, "question_transcription", lambda: client.transcribe(input_audio))
    if not isinstance(recognized_question, str) or not recognized_question.strip():
        raise ModelChainError("empty_transcription")
    query_vector = _measure(
        timings,
        "query_embedding",
        lambda: client.embed([recognized_question], input_type="query")[0],
    )
    document_vectors = _measure(
        timings,
        "passage_embeddings",
        lambda: client.embed([item["text"] for item in passages], input_type="document"),
    )
    dense_passages = _measure(
        timings,
        "dense_ranking",
        lambda: rank_passages(query_vector, document_vectors, passages),
    )
    reranked_indexes = _measure(
        timings,
        "gte_reranking",
        lambda: client.rerank(recognized_question, [item["text"] for item in dense_passages]),
    )
    if sorted(reranked_indexes) != list(range(len(dense_passages))):
        raise ModelChainError("invalid_reranking_response")
    reranked_passages = [dense_passages[index] for index in reranked_indexes]
    evidence = reranked_passages[:evidence_count]
    answer = _measure(
        timings,
        "answer_generation",
        lambda: client.generate(recognized_question, evidence),
    )
    if not isinstance(answer, str) or not answer.strip():
        raise ModelChainError("empty_generation")
    answer_audio = _measure(timings, "answer_synthesis", lambda: client.synthesize(answer))
    _validate_wav(answer_audio)
    answer_transcript = _measure(timings, "answer_transcription", lambda: client.transcribe(answer_audio))
    if not isinstance(answer_transcript, str) or not answer_transcript.strip():
        raise ModelChainError("empty_transcription")
    return {
        "status": "passed",
        "services": services,
        "stage_timings_ms": timings,
        "question_audio_bytes": len(input_audio),
        "recognized_question_characters": len(recognized_question),
        "dense_evidence_ids": [item["id"] for item in dense_passages],
        "evidence_ids": [item["id"] for item in evidence],
        "generated_answer_characters": len(answer),
        "answer_audio_bytes": len(answer_audio),
        "recognized_answer_characters": len(answer_transcript),
    }


def load_fixture(path: Path = FIXTURE_PATH) -> dict[str, Any]:
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ModelChainError("fixture_unavailable_or_invalid") from None
    if (
        not isinstance(fixture, dict)
        or fixture.get("fixture_version") != "synthetic-danish-model-chain-v1"
        or fixture.get("synthetic") is not True
        or not isinstance(fixture.get("question"), str)
        or not isinstance(fixture.get("passages"), list)
    ):
        raise ModelChainError("fixture_invalid")
    return fixture


def fixture_sha256(path: Path = FIXTURE_PATH) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--evidence-count", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        fixture = load_fixture()
        result = run_model_chain(
            ModelClient(timeout_seconds=args.timeout_seconds),
            question=fixture["question"],
            passages=fixture["passages"],
            evidence_count=args.evidence_count,
        )
        result["fixture_version"] = fixture["fixture_version"]
        result["fixture_sha256"] = fixture_sha256()
    except ModelChainError as exc:
        print(json.dumps({"status": "failed", "error_category": exc.category}, sort_keys=True))
        return 1
    except ValueError:
        print(json.dumps({"status": "failed", "error_category": "invalid_runner_configuration"}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
