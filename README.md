# Local-AI-API-LITE

A small, independently deployed GPU inference service for trusted local clients. V1 runs one quantized Qwen3.5-4B model in vLLM and exposes the OpenAI-compatible chat-completions API on the private Docker network `ddu_inference_bus`.

This repository owns the model server, model cache, and serving limits. It does not own application state, prompts or game rules. The history backend and future independent clients use the same resident model through `http://ddu-llm:8080` and the `ddu-narrator` API model alias.

## Start

Requirements: Docker Desktop with Linux containers, WSL2 GPU support, an NVIDIA driver visible to Docker, and enough free disk for the image and model cache. The pinned checkpoint is public; no Hugging Face token is required.

1. Copy `.env.example` to `.env` and set `HF_CACHE_DIR` to an absolute cache path outside this repository.
2. Create the shared network once if it does not already exist:

   ```powershell
   docker network inspect ddu_inference_bus *> $null
   if ($LASTEXITCODE -ne 0) { docker network create ddu_inference_bus }
   ```

3. Validate the resolved Compose configuration and start the service:

   ```powershell
   docker compose config --quiet
   docker compose up -d
   docker compose ps
   docker compose logs -f vllm
   ```

The first start downloads the pinned model into the persistent Hugging Face cache. The service listens on port 8080 inside Docker only. Compose publishes no host port; trusted clients attached to `ddu_inference_bus` use `http://ddu-llm:8080`.

## Verify

Run the independent contract client from a throwaway container on the shared network. From PowerShell in this directory:

```powershell
$scripts = (Resolve-Path .\scripts).Path
docker run --rm --network ddu_inference_bus --mount "type=bind,source=$scripts,target=/app,readonly" python:3.11-slim python /app/check_server.py --base-url http://ddu-llm:8080 --model ddu-narrator
docker run --rm --network ddu_inference_bus --mount "type=bind,source=$scripts,target=/app,readonly" python:3.11-slim python /app/shared_inference_smoke.py --base-url http://ddu-llm:8080 --model ddu-narrator --concurrent
```

These clients check HTTP readiness, the served alias, strict synthetic history/examiner schemas, and completion finish reasons. They print sanitized results and never print prompts, model output, or tokens. Host acceptance and measured results are recorded in [docs/BENCHMARKS.md](docs/BENCHMARKS.md).

Run CPU-only tests and configuration checks without this service:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
ruff check scripts tests
docker compose config --quiet
```

## Service boundaries

- One vLLM container and one model instance serve multiple trusted clients.
- The external `ddu_inference_bus` network and `ddu-llm:8080` alias preserve the history client interface.
- No inference port is published on the host. Do not attach untrusted containers or expose the Docker host.
- The medical-examiner workload is entirely fictional and tests JSON transport only; it makes no diagnostic claims.
- The llama.cpp measurements in the companion project's M1.5 record are historical baseline data, not vLLM results.

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), [docs/API_CONTRACT.md](docs/API_CONTRACT.md), and [docs/CUTOVER_AND_ROLLBACK.md](docs/CUTOVER_AND_ROLLBACK.md) before connecting an application.
