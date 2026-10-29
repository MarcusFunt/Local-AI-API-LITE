# Retrieval inference serving design

**Status:** M1/M2 worker implementations with fake-adapter validation; model installation, worker startup, and host acceptance remain pending.

**Baseline:** `main` at `b6311366df8896ce356b2dfdaf86cc6690179062` (2026-10-29).
**Scope:** CPU embeddings (M1) and CPU reranking (M2). Host resource acceptance, retrieval-quality evaluation, and game integration remain future operator/project gates.

## Deployment boundary

Keep the existing root `compose.yaml` unchanged. It continues to define the single `vllm` service using `vllm/vllm-openai:v0.32.0@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34`, model `RedHatAI/Qwen3.5-4B-quantized.w4a16` at revision `7a613872f394578b0b52b683ff4ac47516b4bcaf`, served as `ddu-narrator`, with the private external `ddu_inference_bus` network and alias `ddu-llm`. It publishes no inference port on the host.

Define retrieval services only in `deploy/retrieval/compose.yaml`, as a separate Compose project. Both services join the existing external network with unique aliases `ddu-embed` and `ddu-rerank`. They expose container port 8080 only; no `ports`, `gpus`, `depends_on`, implicit LLM lifecycle coupling, or restart policy. Only trusted client containers should join this network; the network itself is not authentication.

The application image is built locally from the digest-pinned Python base and fully pinned dependency lock. Use a versioned local image tag for development. The resulting application-image digest is a release/build artifact and must be recorded when an operator builds the image; this design does not claim that an unpublished image already has a verified immutable registry reference. Each service uses the same image with a fixed role command.

## Shared worker contract

Each process offers:

- `GET /health`: fast liveness response that does not wait for model loading or inference.
- `GET /ready`: 200 only when the fixed model and tokenizer/code artifacts loaded successfully; otherwise sanitized 503 with one of `loading`, `missing_artifact`, or `failed`.
- `GET /v1/models`: fixed service alias, task, model revision, capabilities, and limits. Never return local paths.
- A maximum JSON request body of 256 KiB and a 60-second inference deadline.
- One active inference and at most one queued request. Reject any additional outstanding request with 429. CPU thread defaults are bounded (`OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2`).

Malformed JSON or required fields: 400. Oversized body: 413. Unsupported model alias/options or invalid semantic fields: 422. Saturated queue: 429. Model unavailable: 503. Inference timeout: 504. Do not include submitted text, document content, vectors, paths, secrets, raw exception text, or stack traces in responses or logs. Validate all model outputs as finite before returning them.

Normal service startup is offline-only (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`) and loads only the allowlisted local snapshots with `local_files_only=True`. A separate explicit installer accepts only the pinned artifacts and supports a no-write `--plan` plus an explicit `--install`. The installer mounts the operator-selected persistent cache read-write; serving containers mount it read-only. Transformers' verified custom-code module copy uses `/tmp/hf-modules` on the container's bounded ephemeral tmpfs, so startup does not write to the model cache. No HTTP field selects a model ID, revision, path, code, or command.

## M1: embeddings

| Field | Contract |
|---|---|
| Service/model alias | `ddu-embed` |
| Endpoint | `POST /v1/embeddings` |
| Model | [`Qwen/Qwen3-Embedding-0.6B`](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` |
| License/language | Apache-2.0; model card describes 100+ supported languages, including Danish |
| Output | 1024-dimensional, L2-normalized finite float32 vectors; input order preserved |
| Limits | `input_type` required (`query` or `document`); 1–8 non-empty strings, at most 2,048 tokens per string |

Request example:

```json
{
  "model": "ddu-embed",
  "input": ["Hvordan påvirkede krigsskadeserstatninger Versaillestraktaten?"],
  "input_type": "query",
  "encoding_format": "float"
}
```

The response follows the OpenAI embeddings field subset: `object: "list"`, ordered `data` records with `object: "embedding"`, `index`, and `embedding`, plus the fixed model alias and available token usage. Reject unsupported encoding options.

For `query`, use the versioned instruction `Instruct: Given a question, retrieve relevant passages that answer the question\nQuery:` followed by the query. For `document`, encode the plain passage without a query instruction. Return the dimension, normalization, model revision, and prompt-profile version in model metadata. A change to model revision, prompt, pooling, dimension, or normalization changes the embedding fingerprint and requires callers to treat the vectors as a different index space.

The model weights and tokenizer are not installed by this change. Readiness must remain 503 with sanitized `missing_artifact` when the cache is absent, while liveness stays healthy.

For a future operator-approved window, create an absolute cache directory outside the repository, copy `deploy/retrieval/.env.example` to `deploy/retrieval/.env`, and set `MODEL_CACHE_DIR` to that directory. Set `MODEL_CACHE_UID` and `MODEL_CACHE_GID` to numeric IDs that can write to the bind-mounted cache; they control only the explicit installer. For example, on Linux, create the cache directory with `sudo install -d -o 10001 -g 10001 -m 0750 "$MODEL_CACHE_DIR"` when using the sample IDs. Serving workers continue to run as UID/GID 10001 and only read the cache. The explicit installer profile supports a no-write plan followed by a deliberate install of the fixed embedding, reranker, and reviewed-code artifacts:

```sh
docker compose --env-file deploy/retrieval/.env --file deploy/retrieval/compose.yaml --profile install run --rm model-installer --plan
docker compose --env-file deploy/retrieval/.env --file deploy/retrieval/compose.yaml --profile install run --rm model-installer --install
```

Only after separately reviewing the cache and host budget should the operator build/start workers with `docker compose --env-file deploy/retrieval/.env --file deploy/retrieval/compose.yaml up -d embed rerank`. These commands are documentation; this implementation did not run them.

## M2: reranking

| Field | Contract |
|---|---|
| Service/model alias | `ddu-rerank` |
| Endpoint | `POST /v1/rerank` (local API contract) |
| Model | [`Alibaba-NLP/gte-multilingual-reranker-base`](https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base), revision `8215cf04918ba6f7b6a62bb44238ce2953d8831c` |
| Custom implementation | [`Alibaba-NLP/new-impl`](https://huggingface.co/Alibaba-NLP/new-impl/tree/40ced75c3017eb27626c9d4ea981bde21a2662f4), code revision `40ced75c3017eb27626c9d4ea981bde21a2662f4` |
| License/language | Apache-2.0 for the model and reviewed custom-code repository; model card lists 75 languages, including Danish |
| Limits | Non-empty query; 1–20 documents; at most 2,048 tokens per query/document pair; optional valid `top_n` |

Request example:

```json
{
  "model": "ddu-rerank",
  "query": "Hvorfor ønskede Frankrig hårdere fredsbetingelser?",
  "documents": ["Passage A...", "Passage B..."],
  "top_n": 2
}
```

Return records containing original `index` and finite `relevance_score`, ordered by descending score and then ascending original index for ties. Include the fixed alias and model revision; never echo documents. Scores are ranking signals and are not calibrated probabilities or comparable across model changes.

The JSON response uses `model`, `revision`, `results` (each result has `index` and `relevance_score`), and `usage` (`prompt_tokens`, `total_tokens`). Token usage is the sum of query/document pair token counts and does not include model-internal special-token accounting beyond the tokenizer's pair encoding.

The GTE model uses custom Transformers code. The loader sets `trust_remote_code=True` only after verifying the reviewed, pinned model and code revisions from the local cache, with `local_files_only=True`. The installer and loader verify SHA-256 for `configuration.py` (`3411088045ffb8a9a0aa9936eae275896b39983a2ee5b08f091b44e6289e4fe4`) and `modeling.py` (`374670b416fcc82f081c9cd28b5fd61c2bd91bbe18eb4798fcc48a81f9c250a0`). Inference uses bounded chunks of two candidate pairs, rejects overflow rather than truncating, and returns raw finite ranking scores. Floating or request-selected code is prohibited.

## Build and resource pins

| Component | Pin |
|---|---|
| Python base | `python:3.11.18-slim-bookworm@sha256:061c190f3d5815b341a18dfe8a45771f8ba68c15a607ea56997fde3009ac4eec` (linux/amd64) |
| FastAPI | `0.162.0` |
| Uvicorn | `0.54.0` |
| Transformers | `4.57.6` |
| Hugging Face Hub | `0.36.2` |
| PyTorch | `2.7.0` CPU wheel |
| Hugging Face model/code revisions | Exact revisions in the M1/M2 tables above |

Sentence Transformers `5.7.0` was evaluated as the initial candidate. The implementation uses the direct Transformers model/tokenizer path so Qwen's last-token pooling and offline `local_files_only` behavior are explicit, while GTE can bind its custom `code_revision`. This avoids adding unused Sentence Transformers, scikit-learn, and SciPy dependencies to the worker image. The full transitive dependency lock pins exact versions and artifact hashes. Keep model-serving dependencies inside `services/retrieval`; do not add them to the dashboard or repository-wide development requirements.

Initial Compose ceilings are provisional, not measured residency budgets: embedding 4 CPUs / 5 GiB memory; reranking 2 CPUs / 4 GiB memory. Both are CPU-only. These ceilings do not establish that the pair can run resident on the target host. The 2026-10-29 host snapshot found 5.77 GiB free Windows-visible RAM and 9,904 MiB device-wide GPU allocation; neither metric approves a worker launch, and the latter is not per-process usage. Keep actual installation, startup, RSS, memory-pressure, latency, and LLM-impact checks pending for a separately authorized operator window.

## Automated and deferred acceptance

Model-free CI injects fake adapters and checks schemas, bounds, output ordering, finite values, readiness/error sanitization, queue overload, and Compose structure. It must not download model weights or require a GPU. Compose checks must preserve root vLLM configuration, external network usage and aliases, and prove no published inference ports, GPU request, `depends_on`, or restart policy were added.

Host acceptance remains separate: install artifacts explicitly, start one CPU worker at a time, measure actual startup/RSS/CPU and bounded latency, exercise real Danish queries/passages, and confirm the existing LLM remains healthy under an approved window. Retrieval quality against labeled history queries, resident-pair memory, generator impact, and any service lifecycle controls are not accepted here.

The history/game backend owns source ingestion, chunking, vector storage, hybrid retrieval, citations, and gameplay. No database access, dashboard lifecycle UI, game integration, TTS, or STT is part of M0–M2.

## References

- [Qwen3 Embedding model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- [GTE Multilingual Reranker model card](https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base)
- [Reviewed GTE custom code](https://huggingface.co/Alibaba-NLP/new-impl/tree/40ced75c3017eb27626c9d4ea981bde21a2662f4)
- [Sentence Transformers model loading options](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html)
