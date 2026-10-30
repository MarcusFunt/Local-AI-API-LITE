# Retrieval inference serving design

**Status:** M1/M2 worker implementations with fake-adapter validation. A local Windows host smoke acceptance passed on 2026-10-30; the built image and installed model cache remain local artifacts.

**Baseline:** `main` at `b6311366df8896ce356b2dfdaf86cc6690179062` (2026-10-29).
**Scope:** CPU embeddings (M1) and CPU reranking (M2). Broader host resource acceptance, retrieval-quality evaluation, and game integration remain future operator/project gates.

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

Model weights are not bundled in the worker image. During the local operator acceptance recorded below, the pinned model and code snapshots were installed to an external cache and both workers reached ready. If required artifacts are absent, readiness must remain 503 with sanitized `missing_artifact` while liveness stays healthy.

For an installation on another host, create an absolute cache directory outside the repository, copy `deploy/retrieval/.env.example` to `deploy/retrieval/.env`, and set `MODEL_CACHE_DIR` to that directory. Set `MODEL_CACHE_UID` and `MODEL_CACHE_GID` to numeric IDs that can write to the bind-mounted cache; they control only the explicit installer. For example, on Linux, set the same absolute path in the `.env` file and create it with `sudo install -d -o 10001 -g 10001 -m 0750 /absolute/path/to/retrieval-cache` when using the sample IDs. Serving workers continue to run as UID/GID 10001 and only read the cache. The explicit installer profile supports a no-write plan followed by a deliberate install of the fixed embedding, reranker, and reviewed-code artifacts:

```sh
docker compose --env-file deploy/retrieval/.env --file deploy/retrieval/compose.yaml --profile install run --rm model-installer --plan
docker compose --env-file deploy/retrieval/.env --file deploy/retrieval/compose.yaml --profile install run --rm model-installer --install
```

On another host, review the cache location and resource budget before building or starting workers. The command below was run for the local acceptance on 2026-10-30; those two worker containers remain running on that host by operator choice:

```sh
docker compose --env-file deploy/retrieval/.env --file deploy/retrieval/compose.yaml up -d embed rerank
```

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

Compose ceilings are 4 CPUs / 5 GiB for embedding and 2 CPUs / 4 GiB for reranking. Both are CPU-only. The local smoke run below observed both workers resident together within these limits, but it is not a sustained memory-pressure test. The 2026-10-29 GPU allocation snapshot is device-wide and is not per-process usage.

## Automated and deferred acceptance

Model-free CI injects fake adapters and checks schemas, bounds, output ordering, finite values, readiness/error sanitization, queue overload, and Compose structure. It must not download model weights or require a GPU. Compose checks must preserve root vLLM configuration, external network usage and aliases, and prove no published inference ports, GPU request, `depends_on`, or restart policy were added.

The local operator acceptance below exercised real Danish requests and confirmed the existing LLM remained healthy. Retrieval quality against labeled history queries, sustained load, memory-pressure behavior, generator impact under actual generation, and service lifecycle controls remain unaccepted.

## Local operator acceptance — 2026-10-30

- Built `local-ai-api-lite-retrieval:0.1.0` from PR #7 head `4542e658658cc1d5057a3c802da1c92218726657`, which is included in merge commit `dd8273fa724fbd5d8470ac7c2b71f5b281d7cd05`. Local image ID/digest: `sha256:76b81c69aabf7cafdd41d11a6ddad2cf896ea154d53cf8898f4f67f79b5461a0` (1,600,851,438 bytes). This image was not pushed to a registry.
- Ran the explicit installer for the pinned Qwen embedding snapshot, GTE reranker snapshot, and reviewed GTE code snapshot. The reviewed custom-code hashes passed verification. The external cache occupied 1.71 GiB across 76 files; no cache is stored in the repository.
- Both services joined `ddu_inference_bus` on aliases `ddu-embed` and `ddu-rerank`, published no host ports, and returned HTTP 200 for `/health`, `/ready`, and `/v1/models`.
- An ephemeral client on `ddu_inference_bus` called both real inference endpoints. A Danish embedding query returned a finite 1,024-dimensional unit vector in 0.26 seconds. A Danish reranking query over three candidate passages returned two finite, descending relevance scores with original indices in 0.12 seconds. These were one warm request per endpoint, not a benchmark or retrieval-quality evaluation.
- After both workers were ready, `docker stats` reported 2.564 GiB for embedding (5 GiB limit) and 1.719 GiB for reranking (4 GiB limit); Windows showed 5.3 GiB free physical memory. The existing `local-ai-api-lite-vllm-1` remained healthy at 3.536 GiB, and an HTTP 200 `GET /v1/models` through `ddu-llm` returned `ddu-narrator`. No generation request or GPU benchmark was run.
- The operator asked to leave both retrieval workers running after the smoke acceptance.

The history/game backend owns source ingestion, chunking, vector storage, hybrid retrieval, citations, and gameplay. No database access, dashboard lifecycle UI, game integration, TTS, or STT is part of M0–M2.

## References

- [Qwen3 Embedding model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- [GTE Multilingual Reranker model card](https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base)
- [Reviewed GTE custom code](https://huggingface.co/Alibaba-NLP/new-impl/tree/40ced75c3017eb27626c9d4ea981bde21a2662f4)
- [Sentence Transformers model loading options](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html)
