# M3 operational reliability

**Implementation status:** Dashboard status for retrieval and speech services, independent retrieval controls, worker queue observability, the bounded synthetic pipeline benchmark, and fresh-auth DSN reveal are implemented. Automated tests and short live model-chain and retrieval-to-generation smoke runs pass. Live lifecycle recovery, sustained-load, and cold-start acceptance remain operator checks.

This milestone preserves the existing vLLM narrator and the CPU-only Qwen embedding and GTE reranking workers. The retrieval services remain in the independent `deploy/retrieval/compose.yaml` project on `ddu_inference_bus`; this milestone does not add TEI or replace their runtime.

## Implemented

- Dashboard status reports `embed` and `rerank` independently. It probes `/health` for liveness, `/ready` for model readiness, `/v1/models` for aliases and pinned revisions, and `/metrics` for bounded aggregate queue counters. Docker inspection verifies the fixed project/service labels, image tag, private alias, read-only cache mount, no host-published ports, and no privileged or GPU allocation. Unavailable metrics stay unavailable.
- Retrieval workers expose aggregate request counters at `/metrics`. A timed-out model call that is still running marks the worker degraded through `/ready`; the bounded queue rejects new excess work. Because Python cannot safely cancel an active model thread, recovery is an explicit operator restart of that one worker.
- The dashboard has separately configured, authenticated, CSRF-protected Start, Stop, and Restart controls for each retrieval worker. Every action requires confirmation. It validates the source and resolved Compose configuration before acting, uses fixed service names and arguments, and checks readiness plus model identity before reporting successful Start or Restart. It never calls `down -v`.
- Retrieval desired state and a sanitized operation journal persist in the dashboard SQLite database. Startup reconciles interrupted operations by inspecting runtime state only. It does not start or stop a service. Restart is skipped when the operator's desired state is stopped.
- `scripts/retrieval_pipeline_benchmark.py` runs a versioned, invented Danish workload through embedding, reranking, and narrator generation. Component baselines, full pipeline, concurrency 1 or 2, bounded repeat/query/time limits, readiness monitoring, redacted request hashes, and Docker/host resource sampling are supported. It never starts, stops, or reconfigures services. Synthetic results measure transport and serving behavior; they do not measure historical retrieval quality or answer accuracy.

## Configure retrieval controls

Create the deployment directory from the repository's existing retrieval Compose project. Copy `.env.example` to `.env`, set `MODEL_CACHE_DIR` to the existing absolute model-cache path, and set the installer UID/GID values as appropriate for that host. Do not put credentials or model data into the Compose project directory.

Configure the dashboard from `dashboard/backend` using the path to the directory containing `compose.yaml` and `.env`:

```powershell
python -m app.settings configure-retrieval-compose-root --path D:\deploy\Local-AI-API-LITE\deploy\retrieval
```

The command stores only the explicitly selected project path in local dashboard settings and validates the fixed source contract. Before any dashboard lifecycle request, the controller also asks Docker Compose to resolve and validate the complete configuration. Retrieval configuration is independent of the narrator Compose root.

The dashboard's authenticated `GET /api/control/v1/retrieval-operations/state` response reports configured state, per-worker desired state and last operation, plus the shared activity gate. The fixed POST routes are:

```text
POST /api/control/v1/retrieval-operations/{embed|rerank}/{start|stop|restart}
```

Each body must be `{"confirm": true}` and each request must carry the dashboard session and CSRF protections. The browser presents a per-service confirmation dialog. A restart request is intentionally skipped when that worker's desired state is stopped; use Start to change a deliberately stopped worker to running.

## Synthetic pipeline benchmark

Run only against already-started services in an operator-approved test window. The default URLs are private Docker aliases. `--help` shows all bounded choices. For a small warm run, start with one query, one repetition, one client, and no stability phase:

```powershell
python scripts/retrieval_pipeline_benchmark.py --run-mode warm --concurrency 1 --max-queries 1 --repeats 1 --warmup 0 --stability-seconds 0 --output-dir artifacts\retrieval-benchmark\warm-smoke
```

For a cold-start observation, start the runner first with `--run-mode cold`; separately start the desired worker processes from an operator terminal while it waits. The runner measures time spent waiting for readiness but does not initiate process or model loading. Model download time is outside this measurement. A longer stability run requires its own approved window and an explicit `--stability-seconds` value.

The output directory contains `results.jsonl`, `report.json`, and `report.md`. Request traces contain a fixture question ID and SHA-256 hash, stage timings, outcome, error category, and token counts; they do not contain question text, candidate passages, or generated answers. Reports include the exact fixture hash, expected and reported model revisions, image IDs when Docker can provide them, measured versus unavailable metrics, and device-wide GPU measurements with an attribution warning. Do not place report output in a public or shared location without reviewing its metadata.

## Full model-chain integration smoke

`scripts/model_chain_smoke.py` sends one versioned fictional Danish question through the live models in sequence: TTS creates question audio, ASR returns the query text, Qwen embeds that query and the synthetic passages, local cosine ranking orders the passages, GTE reranks them, the narrator writes an answer from the evidence, and TTS plus ASR process the answer audio. The runner checks service health/readiness and aliases before inference, verifies each API-reported model revision, and reports timings and output sizes without recording prompt, passage, transcript, answer, or audio content. The narrator API does not report its revision, so its output explicitly marks that revision as unverified.

Run it from the repository root in a short-lived client container attached to the existing private network. This command does not start, stop, or reconfigure any model service:

```powershell
$repo = (Resolve-Path .).Path
docker run --rm --network ddu_inference_bus `
  --mount "type=bind,source=$repo,target=/workspace,readonly" `
  --workdir /workspace --entrypoint python `
  local-ai-api-lite-speech:0.1.0 scripts/model_chain_smoke.py
```

The client image only supplies Python; model requests go to the already-running services. The fixture is fictional and tests wiring, schemas, and successful inference, not historical retrieval quality or answer accuracy. `python -m pytest tests/test_model_chain_smoke.py` covers the dense ordering and output hand-offs without requiring running models.

## Acceptance boundaries

| Area | Automated in this worktree | Still requires a host/operator window |
|---|---|---|
| Read-only status | Fake Docker records for stopped, loading, degraded, wrong identity, timeout and unavailable states; bounded response parsing and sanitized metrics | Short live status and resource snapshots passed; repeat during sustained load and recovery |
| Worker reliability | Queue timeout, stuck-inference degraded readiness, saturation and aggregate metrics | Exercise real pinned-model load failure, queue stall and recovery |
| Persistence and isolation | Durable desired state/journal, shared activity gate, and controller restart reconciliation that performs inspection only | Restart only the controller and verify retained sessions/configuration against the host's actual data directory |
| Retrieval controls | Authentication, CSRF, confirmation, fixed service/project contract and readiness/model identity checks | Start, stop and restart each retrieval worker independently; verify narrator identity/health remains unchanged |
| Benchmark | Fixture, redaction, bounded settings, stage accounting, readiness failure handling and resource sampler tests | Warm single-request target-host smoke passed; cold-start and sustained stability trials remain |

Do not use `docker compose down -v` as a recovery step. If a worker is degraded, inspect its sanitized dashboard state and worker logs, then use the per-service restart control only when the operator has selected that recovery action. These controls do not manage the narrator.

## Bounded live smoke check — 2026-10-31

The already-running host containers were inspected and queried without rebuilding or changing any model service lifecycle. Retrieval and speech workers were Docker-healthy; all returned HTTP 200 for `/health`, `/ready`, and `/v1/models` and reported their pinned revisions. The narrator returned HTTP 200 for `/health`, `/v1/models`, and synthetic completions. `scripts/model_chain_smoke.py` passed its fictional TTS → ASR → Qwen embedding → local cosine ranking → GTE reranking → narrator → TTS → ASR handoff. It verified the API-reported revisions for embedding, reranking, STT, and TTS. The narrator API does not report a revision, so its pin remains unverified. The script prints only timings, IDs and output sizes, not generated content.

The live retrieval containers had no published host ports and both used image ID `sha256:76b81c69aabf7cafdd41d11a6ddad2cf896ea154d53cf8898f4f67f79b5461a0`. Their `/metrics` endpoint returned HTTP 404. The status reader and benchmark therefore report queue counters as unavailable, not zero. The source worker implementation in this worktree exposes the sanitized metrics route; applying it to the live containers requires an image build and an operator-selected lifecycle window.

This is a one-request-per-endpoint warm smoke check. It does not establish retrieval quality, cold-start latency, sustained stability, restart isolation, failure recovery, or combined-load resource acceptance.

The synthetic benchmark was run on the Docker host against those already-running services with concurrency `1`, one query, one repetition, no warmup, and no stability phase. All four modes succeeded with no errors or timeouts: embedding-only 664 ms, reranking-only 143 ms, generation-only 993 ms, and the full pipeline 1,387 ms (embedding 609 ms, local dense ranking 0.15 ms, GTE reranking 145 ms, generation 633 ms). Each mode has one sample, so its p50 and p95 are that single observation, not estimates of typical performance.

The host-side sampler collected two Docker/GPU samples. Peak container memory was 3.23 GiB / 5 GiB for embedding, 1.79 GiB / 4 GiB for reranking, and 3.67 GiB for the narrator container. Peak CPU samples were 118.47%, 0.14%, and 25.05% respectively. Device-wide GPU utilization reached 100%, with 10.31 GiB / 12 GiB used. Host RAM usage peaked at 25.36 GiB / 31.77 GiB, with a minimum 6.41 GiB available. GPU values are device-wide and cannot be attributed to one service. Two samples during a one-query run do not establish sustained-load headroom. The old retrieval images returned 404 from `/metrics`, so benchmark queue counters remain unavailable.

The sanitized run report and request hashes are in `artifacts/retrieval-benchmark/live-smoke-20261031/`. The live smoke and benchmark used synthetic invented text and did not measure historical retrieval relevance or answer factuality.

## Test commands

From the repository root and the indicated project directories:

```powershell
python -m pytest tests
python -m pytest tests
cd dashboard/backend
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\ruff.exe check app tests
cd ..\frontend
npm test -- --run
npm run build
```

Verification on 2026-10-31: root worker/benchmark tests `99 passed`; dashboard backend tests `195 passed`; frontend tests `40 passed`; TypeScript/Vite production build passed; backend Ruff checks passed. The live model-chain smoke and synthetic pipeline run also passed. These unit tests use fake adapters; the live runs use existing synthetic fixtures. No model service was stopped, rebuilt, or reconfigured during these checks.
