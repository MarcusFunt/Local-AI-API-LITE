# Local dashboard

The dashboard is a local status and administration UI for Local-AI-API-LITE. It provides authenticated inference status and lifecycle controls, profile display, host telemetry, job history, sanitized service logs, and local administrator/session settings.

## Requirements

Use Python 3.11 or newer and Node.js 22.12 or newer. Docker Desktop must be visible to the same Windows user who runs the controller. NVIDIA driver/NVML support is optional; unavailable telemetry is reported as unavailable, never as zero.

## Install for the current Windows user

From the repository root, run:

```powershell
.\dashboard\scripts\install_dashboard.ps1
```

The installer builds the frontend, creates `dashboard/backend/.venv`, installs the dashboard runtime, and registers and starts a current-user Scheduled Task named `Local-AI-API-LITE Dashboard Controller`. The task has an interactive sign-in trigger, limited run level, and three restart attempts at one-minute intervals. Its only application command runs Uvicorn at `127.0.0.1:8765`. It does not run Compose, start inference, or change Docker Desktop settings. It runs in the interactive Windows account that can access Docker Desktop, not as a machine-wide service, and is unavailable before that user signs in.

On first installation, the script prints the one-time setup token to the local installation console. Open <http://127.0.0.1:8765>, submit the token, and choose a permanent administrator password. The token expires after 30 minutes, is single-use, and is stored only as a digest. Re-running the installer after an administrator is configured does not issue a setup token.

Administrator setup, password recovery, and password changes require 8–256 characters, including at least one uppercase letter, one lowercase letter, and one digit. Symbols are optional.

Remove the current user's task and its controller process without deleting runtime data:

```powershell
.\dashboard\scripts\uninstall_dashboard.ps1
```

Uninstall does not remove metadata or vault files and does not stop or modify inference containers, volumes, or networks.

## Run manually

The controller can be started in a terminal without installing a Scheduled Task:

```powershell
Push-Location .\dashboard\frontend
npm ci
npm run build
Pop-Location
Push-Location .\dashboard\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[gpu]"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Run Python module commands from `dashboard/backend` so Python can import the `app` package. For frontend development, run `npm run dev` from `dashboard/frontend`; Vite proxies the local API to port 8765. The production frontend is served from `dashboard/frontend/dist`.

## Administrator setup and recovery

To issue a replacement initial setup token, or recover a configured administrator, run one of these commands from `dashboard/backend` as the same Windows account. The token appears only in the local terminal; do not copy it into chat, logs, screenshots, tickets, or source control.

```powershell
.\.venv\Scripts\python.exe -m app.bootstrap issue-token
.\.venv\Scripts\python.exe -m app.bootstrap recover-admin
```

`issue-token` is valid only before initial setup. It invalidates any earlier unused setup token. `recover-admin` revokes active dashboard sessions, invalidates any earlier recovery token, and prints a new one-use token that expires after 30 minutes. Recovery changes the password only after the token is submitted through the local UI and does not delete vault values.

Dashboard sessions last eight hours and use a server-managed HttpOnly cookie. The browser keeps its CSRF value only in memory, so reloading the page requires signing in again. Sign-out, password change, session revocation, and recovery revoke server-side sessions as appropriate. Dashboard authentication is separate from Windows and Docker Desktop authentication.

## Local data and security boundary

On Windows, runtime metadata lives under `%LOCALAPPDATA%\Local-AI-API-LITE\Dashboard\metadata.sqlite3`. The containing directory is restricted to the current Windows user. Encrypted vault values are stored separately from descriptive metadata. The random vault root key is protected with Windows DPAPI CurrentUser and belongs at `%LOCALAPPDATA%\Local-AI-API-LITE\Dashboard\vault\root-key.dpapi`; it is never stored in plaintext. If the current user's Windows profile or DPAPI key is unavailable, the vault is locked and does not fall back to plaintext. The installer does not create or delete vault data.

The Scheduled Task runs only in the Docker Desktop user's interactive session, with limited privileges. The controller binds to loopback and performs fixed Docker queries. It does not mount a Docker socket or accept arbitrary commands. The model endpoint remains private on `ddu_inference_bus` with no published host port. Inference controls stay unavailable until an operator configures a stable Compose project root in local settings; that configuration alone does not start or change inference.

## Explicit Compose project configuration

The controller never guesses which checkout owns the inference service. After selecting a stable checkout, configure its root locally from `dashboard/backend`:

```powershell
.\.venv\Scripts\python.exe -m app.settings configure-compose-root --path "D:\path\to\Local-AI-API-LITE"
```

The command validates the pinned Compose project, external `ddu_inference_bus` network, `ddu-llm` alias, model-cache bind mount, and absence of published host ports. It then stores the chosen path in the current user's protected dashboard settings. Docker Compose configuration validation is read-only; this command does not start, stop, recreate, or reconfigure containers. Until an operator explicitly runs it, status controls have no configured project source and remain unavailable. Never configure a worktree that may be deleted or replaced as the long-term project root.

## Authenticated read-only API

- `GET /api/control/v1/controller/health` is minimal controller liveness and does not consult inference.
- `GET /auth/state` reports whether one-time setup is required and whether the browser's HttpOnly cookie has an active session.
- `GET /api/control/v1/status` requires an administrator session and returns cached inference state, the observed runtime configuration, telemetry, and snapshot freshness/error metadata.
- `GET /api/control/v1/telemetry`, `GET /api/control/v1/profiles`, and `GET /api/control/v1/operations/state` require an administrator session.
- `POST /api/control/v1/operations/start`, `/stop`, `/restart`, and `/apply-profile` require an administrator session and CSRF token. Start and profile changes accept only a versioned profile ID; the API exposes no shell, Compose argument, prompt, or arbitrary command input.
- Job history, status, results, comparisons, and cleanup previews require an administrator session. Job start, cancellation, and confirmed cleanup also require a CSRF token.
- `GET /api/control/v1/logs` requires an administrator session and returns a bounded, redacted tail from the fixed `vllm` Compose service.
- `GET /api/control/v1/databases` requires an administrator session. Adding, updating, checking, revealing, or removing a database monitor requires an administrator session and CSRF token.

## Inference operations

The Models page offers fixed Start, Stop, Restart, and Apply Profile actions. Each action requires an explicit confirmation. Controls remain disabled until an operator configures a stable Compose project root using the local CLI. Only one inference or benchmark activity can hold the shared operation gate at a time.

The dashboard records desired state and the latest operation in local SQLite metadata. Desired state starts as `stopped`, and a deliberate Stop remains stopped after a controller or browser restart; starting inference always requires an operator action. Long model loading is shown as an active operation. Candidate failures report whether a last-known-good profile was restored. Profile activation waits for container health and the fixed `/health` and `/v1/models` contract checks before reporting success. Those checks do not send prompts or collect request/response content.

The fixed controller operations can start, stop, or recreate only the validated `vllm` service in the explicitly configured project. They do not run `compose down`, remove containers or volumes, change networks, publish host ports, or accept arbitrary commands. A profile switch preserves the model-cache volume and the stable `ddu-llm` alias. Dashboard restart reconciliation is read-only and does not start or stop inference automatically.

## Predefined checks, benchmarks, and history

The Benchmarks page exposes only three fixed jobs: server readiness, the existing synthetic API smoke cases, and a bounded performance benchmark. A job runs inside an ephemeral, resource-limited Docker client on `ddu_inference_bus`, with the pinned vLLM image, read-only access to the repository scripts, and a private per-job artifact directory. The dashboard passes fixed arguments and does not provide a shell or free-form command field. Jobs share the single-activity gate with inference lifecycle changes. Benchmark concurrency defaults to one; two is available for normal comparison, while four requires an explicit diagnostic choice.

Job metadata is stored in the dashboard's local SQLite database and artifacts under its per-user data directory, outside the repository. Job output is size limited and projected onto an allowlist before it is stored; prompts, completions, credentials, and full HTTP error bodies are discarded. The database retains at most 500 job rows, while sanitized JSONL artifacts are capped at 30 files and 50 MiB. When metadata history reaches its cap, preview cleanup and explicitly confirm it before starting more jobs. Results compare as comparable only when job type, profile/revision, engine image, context/sequence settings, host/GPU, and script version match. Differences are shown instead of combining incompatible measurements.

The page polls job history while a job is queued or running. A cancellation request stops only the dashboard's ephemeral job container. It does not stop or reconfigure the inference service. The controller does not collect benchmark prompt or response content.

## Sanitized service logs

The Logs page reads at most 200 recent lines from the fixed `vllm` Compose service. It discovers the container through the pinned Compose project and service labels, reads its environment only in memory to redact credential values, then filters credential assignments, bearer tokens, and filesystem paths before returning the result. If the output contains prompt, completion, message, or request-body fields, the content-bearing output is replaced with a single notice. The controller does not save log copies; browser export uses only the already-sanitized response.

The status handler reads from memory immediately while a single bounded worker refreshes Docker status and host telemetry. A successful Docker query with no matching container is `Stopped`; a failed query is `Unknown/Unavailable`. The UI polls every five seconds while visible, backs off to thirty seconds while hidden, allows one in-flight status request, and keeps up to 120 samples in browser memory. It does not write metrics or model data to disk.

Database monitoring is optional and remains `Unconfigured` until an administrator adds a connection. GPU utilization and VRAM are device-wide and can include Windows display activity and other processes.

## Optional PostgreSQL monitoring

Add a monitor from the Databases page with a PostgreSQL connection URI. The URI is encrypted in the local vault with AES-GCM and a root key protected by Windows DPAPI CurrentUser; list, update, check, and delete responses never include it. An administrator can request a one-time reveal with a CSRF-protected action; that response is marked `no-store`, and the page keeps the value only in component memory until hidden or navigated away from.

Each check runs in one isolated bounded worker, uses the vault's `database-monitor` purpose, applies connect and statement timeouts, forces PostgreSQL's default transaction mode to read-only, and executes only `SELECT 1`. Probe failures are reduced to short categories and cannot block inference status. Database monitoring does not provision or alter accounts, schemas, or data. An empty configuration reports `Unconfigured`; a saved monitor that cannot be reached reports `Unavailable`.

## Checks

From the repository root:

```powershell
.\dashboard\backend\.venv\Scripts\python.exe -m pytest -q
.\dashboard\backend\.venv\Scripts\python.exe -m ruff check scripts tests dashboard\backend\app dashboard\backend\tests
npm --prefix .\dashboard\frontend ci
npm --prefix .\dashboard\frontend test
npm --prefix .\dashboard\frontend run typecheck
npm --prefix .\dashboard\frontend run build
docker compose --env-file .env.example config --quiet
```

## Pending manual host acceptance

These checks require a separately authorized operator window. Do not stop, restart, reconfigure, or benchmark the live inference service as part of normal implementation or CI.

- [ ] Install and uninstall the named task as a standard user; verify the controller runs only after sign-in and stops at sign-out.
- [ ] Confirm Docker Desktop and the controller use the same Windows account; verify Docker unavailable reports `Unknown` and recovers when Docker is available.
- [x] Confirm controller access is loopback-only; verify the model API has no host-published port and remains on `ddu_inference_bus`. A bounded GET check also confirmed the `ddu-narrator` model alias; see [DASHBOARD_ACCEPTANCE.md](DASHBOARD_ACCEPTANCE.md).
- [ ] Verify first-run setup, sign-in, reload reauthentication, sign-out, session revocation, password change, recovery, and current-user runtime ACLs.
- [ ] Verify DPAPI CurrentUser behavior in this account and from a different Windows user; the other user must see a locked vault.
- [ ] Record NVML availability, host/controller CPU and RAM, and controller startup cost during a normal workload without changing inference.
- [ ] Verify expected model alias, revision, image digest, no prompt/response collection, and no credential values in status or metadata.

The current-user Scheduled Task was installed and launched from the stable `D:\projects\Local-AI-API-LITE` checkout on 2026-10-29. Its liveness endpoint and loopback binding were verified; first-run administrator setup is still required. Sign-in/sign-out lifecycle, Docker Desktop loss/recovery, cross-user DPAPI, and live inference loading/rollback checks remain pending. See [DASHBOARD_ACCEPTANCE.md](DASHBOARD_ACCEPTANCE.md) for the dated host evidence. Existing Phase 0 values below are historical baseline observations only.

See [DASHBOARD_SECURITY.md](DASHBOARD_SECURITY.md) for the security boundary and [DASHBOARD_ACCEPTANCE.md](DASHBOARD_ACCEPTANCE.md) for the dated automated evidence and pending operator checks.

## Phase 0 baseline capture

On 2026-10-28, `docker compose --env-file .env.example config --quiet` passed. The existing `local-ai-api-lite-vllm-1` container was healthy, `/health` and `/v1/models` returned the expected `ddu-narrator` alias, and both existing synthetic structured-output workloads passed. Container inspection showed the pinned vLLM 0.32.0 image digest, pinned Qwen revision, 8192 context, four sequences, 0.75 GPU memory utilization, and no published host port. This was the already-running service; the dashboard did not change it.

One short point-in-time sample during the same capture window showed host CPU at 4.9%, host RAM at 21.2/31.8 GiB used, and 25.0 GiB free on the model-cache volume. Docker reported 1.14% container CPU and 3.4/29.4 GiB container memory. Device-wide NVIDIA telemetry reported 7% utilization, 10,308/12,288 MiB VRAM used, 34°C and 8.87 W. These figures are observations from this host, not budgets or targets; VRAM includes other GPU processes.
