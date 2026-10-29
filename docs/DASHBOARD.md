# Local dashboard

The Phase 1 dashboard is a local, read-only status page for the existing Local-AI-API-LITE inference service. It shows the container health state, observed model profile, host and device telemetry, and a short in-browser sample history. It does not start, stop, restart, or reconfigure inference.

## Run locally on Windows

Requirements: Python 3.11+, Node.js 20.19+ (or 22.12+), Docker Desktop with the existing `local-ai-api-lite` Compose project visible to the current Windows user, and an optional NVIDIA driver/NVML installation for GPU metrics.

Build the static frontend:

```powershell
Push-Location .\dashboard\frontend
npm ci
npm run build
Pop-Location
```

Install and run the controller from its directory so Python can import the `app` package:

```powershell
Push-Location .\dashboard\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,gpu]"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765>. The controller binds to loopback. Its Docker integration issues only fixed `docker ps`, `docker inspect`, and `docker stats --no-stream` queries. It does not mount a Docker socket or run Compose mutations. The model endpoint remains private on `ddu_inference_bus`.

For frontend development, keep the controller running in one terminal, then run `npm run dev` from `dashboard/frontend`; Vite serves the UI on loopback and proxies `/api` to port 8765. The backend serves the production build from `dashboard/frontend/dist` after `npm run build`.

Install the optional `gpu` extra for NVML metrics. If NVML or the NVIDIA driver is unavailable, GPU readings display as unavailable; they are never represented as zero. VRAM readings are device-wide and include Windows display and other GPU processes. If Docker is not reachable, inference state is unknown rather than stopped. A successful Docker query with no matching container means stopped.

## Read-only API

- `GET /api/control/v1/controller/health` reports controller health without consulting inference.
- `GET /api/control/v1/status` returns inference state, the observed runtime configuration, host/container metrics, and separate timestamps for the latest Docker health probe and latest successful probe.
- `GET /api/control/v1/telemetry` returns current host and GPU metrics.
- `GET /api/control/v1/profiles` returns the two host-tested profiles in `dashboard/config/profiles.yaml`.

No mutation, database, benchmark, or log routes are present in this phase. Profile cards describe validated settings but cannot apply them. The page polls every five seconds while visible, backs off to thirty seconds while hidden, and keeps at most 120 telemetry samples in browser memory. It stores no metrics or model data on disk.

## Checks

From the repository root:

```powershell
Push-Location .\dashboard\backend
.\.venv\Scripts\python.exe -m pytest ..\..\tests -q
.\.venv\Scripts\python.exe -m pytest .\tests -q
.\.venv\Scripts\ruff.exe check .\app .\tests
Pop-Location
npm --prefix .\dashboard\frontend test
npm --prefix .\dashboard\frontend run build
docker compose --env-file .env.example config --quiet
```

The manual host acceptance checklist remains necessary for WSL2/Docker Desktop behavior, NVIDIA telemetry, loopback exposure and measured resource cost. The current implementation does not claim those long-duration measurements.

## Phase 0 baseline capture

On 2026-10-28, `docker compose --env-file .env.example config --quiet` passed. The existing `local-ai-api-lite-vllm-1` container was healthy, `/health` and `/v1/models` returned the expected `ddu-narrator` alias, and both existing synthetic structured-output workloads passed. Container inspection showed the pinned vLLM 0.32.0 image digest, pinned Qwen revision, 8192 context, four sequences, 0.75 GPU memory utilization, and no published host port. This was the already-running service; the dashboard did not change it.

One short point-in-time sample during the same capture window showed host CPU at 4.9%, host RAM at 21.2/31.8 GiB used, and 25.0 GiB free on the model-cache volume. Docker reported 1.14% container CPU and 3.4/29.4 GiB container memory. Device-wide NVIDIA telemetry reported 7% utilization, 10,308/12,288 MiB VRAM used, 34°C and 8.87 W. These figures are observations from this host, not budgets or targets; VRAM includes other GPU processes.
