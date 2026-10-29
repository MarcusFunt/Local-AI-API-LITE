# Retrieval Inference Expansion Implementation Plan

> **For agentic workers:** execute this plan task-by-task in the current session. Keep the milestones in separate commits.

**Goal:** Add independently managed, CPU-only embedding and reranking APIs while preserving the existing vLLM deployment and private-network contract.

**Architecture:** Build one small Python worker image from a digest-pinned Python base and a fully pinned dependency lock. Run it as two independent services on the external `ddu_inference_bus`, with fixed model adapters, offline-only loading from an explicit persistent cache, fake-adapter CI tests, and no host-published ports.

**Tech Stack:** Python 3.11, FastAPI 0.162.0, Uvicorn 0.54.0, Transformers 4.57.6, Hugging Face Hub 0.36.2, PyTorch 2.7.0 CPU, Docker Compose. Sentence Transformers 5.7.0 is evaluated but not retained unless the pinned Qwen recipe can be implemented with its offline and pooling contracts without adding unused dependencies.

**Spec:** `C:/Users/marcu/Downloads/Local-AI-API-LITE_Inference_Expansion_Plan_2026-10-29 (1).md`, phases 0–2.

## Global Constraints

- Preserve root `compose.yaml`, the vLLM service identity/image/model revisions, alias `ddu-llm`, external `ddu_inference_bus`, and no published ports.
- Keep retrieval deployment in `deploy/retrieval/compose.yaml`; it must not depend on or start/restart vLLM.
- Use fixed aliases and pinned model revisions; HTTP requests cannot choose model IDs, paths, code, or commands.
- Embed with `Qwen/Qwen3-Embedding-0.6B` revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`; return 1024-dimensional L2-normalized float32 vectors.
- Require explicit embedding `input_type` (`query` or `document`); use a versioned English query instruction and no document instruction.
- Bound JSON bodies to 256 KiB, embedding batches to 8 strings and 2,048 tokens per input, reranking to 20 candidate pairs and 2,048 tokens per pair, and request wait time to 60 seconds.
- Map malformed input to 400, oversized bodies to 413, unsupported aliases/options to 422, capacity to 429, not-ready models to 503, and inference timeout to 504.
- Rerank with `Alibaba-NLP/gte-multilingual-reranker-base` revision `8215cf04918ba6f7b6a62bb44238ce2953d8831c`; its reviewed custom implementation is `Alibaba-NLP/new-impl` revision `40ced75c3017eb27626c9d4ea981bde21a2662f4`.
- Run inference on CPU only, one active inference plus one queued request per worker, bounded text/batch limits, no automatic model downloads, and no restart policy until desired-state behavior exists.
- Model-free tests use injected fake adapters; do not install model weights, start new containers, benchmark the GPU, or alter Windows tasks or databases in this work.
- Logs and errors must not contain request text, passages, vectors, filesystem paths, secrets, stack traces, or raw HTTP errors.

## Review Focus

- Missing model cache must leave liveness healthy while readiness returns a sanitized 503; test `test_missing_artifact_is_not_ready_but_health_is_live`.
- Invalid JSON/schema responses must not echo submitted text; test `test_validation_error_does_not_echo_request_text`.
- A full inference queue must reject further work with 429 without releasing the active slot; test `test_third_outstanding_request_is_rejected`.
- Non-finite fake model output must never be returned as a vector or score; test `test_non_finite_model_output_is_sanitized`.
- Oversized body and token inputs must be rejected before model execution; test `test_oversized_input_is_rejected_without_adapter_call`.

---

### Task 1: M0 baseline and serving design

**Files:**
- Modify: `docs/DASHBOARD_ACCEPTANCE.md`
- Create: `docs/RETRIEVAL_SERVING.md`
- Create: `docs/superpowers/plans/2026-10-29-inference-retrieval.md`

**Interfaces:** No code dependencies. Record base SHA `b6311366df8896ce356b2dfdaf86cc6690179062` and verified current `ddu-llm` contract.

- [x] Update PR history to mark PR #6 merged; distinguish the packaged-process SQLite redirection finding from controller-restart persistence, which remains unverified.
- [x] Add separate pending acceptance rows for controller restart persistence of session, selected profile, operation journal, and desired state; retain pending Docker-loss/recovery, test PostgreSQL, and cross-user DPAPI checks.
- [x] Record only the read-only 2026-10-29 host observations and the fresh baseline test/build results; state that model-worker acceptance is pending.
- [x] Document the M1/M2 aliases, endpoints, limits, cache behavior, model/code SHAs, dependency pins, resource caps, licensing, and deferred host/game acceptance.
- [x] Commit as `docs: record retrieval serving baseline and design`.

### Task 2: M1 CPU embedding worker

**Files:**
- Create: `services/retrieval/requirements.in`
- Create: `services/retrieval/requirements.lock`
- Create: `services/retrieval/Dockerfile`
- Create: `services/retrieval/retrieval_worker/{__init__.py,models.py,contracts.py,queue.py,app.py,embedding.py,install_models.py}`
- Create: `deploy/retrieval/compose.yaml`
- Create: `deploy/retrieval/.env.example`
- Create: `tests/test_retrieval_workers.py`
- Create: `tests/test_retrieval_deployment.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:** Provides `create_embedding_app(adapter_factory=...)`, `EmbeddingRequest`, `EmbeddingResponse`, and model registry metadata. The reranker task consumes the shared error/readiness/queue helpers and image.

- [x] Add the embedding and deployment tests first, including ordered 1024-vectors, required mode, request bounds, sanitized errors, non-finite adapter output, oversized input rejected before adapter calls, queue overload, offline missing-artifact state, and no-port/no-GPU Compose configuration.
- [x] Run the named tests and confirm they fail because the embedding worker is absent.
- [x] Implement typed request/response contracts and a fake-injectable FastAPI app with `/health`, `/ready`, `/v1/models`, and `/v1/embeddings`.
- [x] Implement fixed Qwen3 loading from the pinned local Hub snapshot with `local_files_only=True`, query-only instruction, document plain text, bounded token counts, and normalized 1024-D float32 output.
- [x] Implement sanitized validation/inference errors and a bounded single-worker queue; never return raw exceptions or log request bodies.
- [x] Add an explicit `--plan`/`--install` artifact installer for only the pinned embedding/model-code snapshots; normal startup stays offline.
- [x] Add the independent CPU-only `ddu-embed:8080` Compose service with read-only model cache, provisional CPU/RAM caps, external network alias, and no `ports`, `gpus`, `depends_on`, or restart policy.
- [x] Add CI validation for retrieval Compose rendering and run the worker/deployment tests; keep all existing CI commands intact.
- [x] Commit as `feat: add offline CPU embedding worker`.

### Task 3: M2 CPU reranking worker

**Files:**
- Modify: `services/retrieval/retrieval_worker/{models.py,contracts.py,app.py,install_models.py}`
- Create: `services/retrieval/retrieval_worker/reranking.py`
- Modify: `deploy/retrieval/compose.yaml`
- Modify: `docs/RETRIEVAL_SERVING.md`
- Modify: `tests/test_retrieval_workers.py`
- Modify: `tests/test_retrieval_deployment.py`

**Interfaces:** Consumes Task 2's shared queue, readiness, error, request-size, and model-metadata helpers; provides `create_reranking_app(adapter_factory=...)` and `/v1/rerank`.

- [ ] Add fake-adapter tests first for score order, deterministic index tie-break, original-index preservation under `top_n`, invalid `top_n`, pair token bounds, missing artifacts, and sanitized output.
- [ ] Run the named tests and confirm the rerank route/adapter is absent.
- [ ] Implement fixed GTE pair scoring on CPU with reviewed `trust_remote_code=True` bound to both exact model and code revisions, `local_files_only=True`, and explicit overflow rejection.
- [ ] Return descending `{index, relevance_score}` signals only; scores are not probabilities, document text is never echoed, and tie order is ascending original index.
- [ ] Add `ddu-rerank:8080` to the same separate Compose project with alias, read-only cache, CPU/RAM caps, and no inference ports or LLM dependency.
- [ ] Run focused tests, all existing repository tests/lint/frontend checks, both Compose config validations, and `git diff --check`.
- [ ] Commit as `feat: add offline CPU reranking worker`.

## Deferred by the source specification

No TTS/STT, dashboard lifecycle controls, history-backend/game integration, PostgreSQL schema or RAG changes, model-weight installation, container startup, host acceptance, GPU benchmark, or PR publication in these milestones. Those require the plan's later acceptance gates and, for host execution, a separate operator window.
