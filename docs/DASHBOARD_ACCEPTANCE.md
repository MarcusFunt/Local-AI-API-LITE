# Dashboard acceptance record

**Record date:** 2026-10-29
**Branch:** `codex/fix-dashboard-runtime-state`
**Pull requests:** [PR #3](https://github.com/MarcusFunt/Local-AI-API-LITE/pull/3), [PR #4](https://github.com/MarcusFunt/Local-AI-API-LITE/pull/4), and [PR #5](https://github.com/MarcusFunt/Local-AI-API-LITE/pull/5) are merged. [PR #6](https://github.com/MarcusFunt/Local-AI-API-LITE/pull/6) is the open follow-up for runtime-state reconciliation and Task 15 acceptance updates.

## Current status — 2026-10-29

The apparent database mismatch was traced to Windows package file redirection. A Codex-launched process requested the expected metadata path, but Windows resolved its open file handle to a separate file under `Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\Local-AI-API-LITE\Dashboard`. SQLite's reported database path alone did not reveal the redirection. A temporary read-only diagnostic launched by Task Scheduler in the non-packaged user context resolved the handle to the expected `%LOCALAPPDATA%` path and found one administrator, 12 sessions, and three operations; the latest operation completed at approximately 19:43:12Z, consistent with the live dashboard. The running controller's in-memory store path was not directly inspected. No database or credentials were changed, and the temporary diagnostic and task were removed.

The observations in the historical updates below reflect what was known at the time; their unresolved-mismatch conclusions are superseded by this finding. Controller-restart persistence and the other pending acceptance checks still require their own tests.

- The operator manually signed in. The authenticated Models page and a fresh read of `/api/control/v1/operations/state` reported `configured: true`.
- At 20:18 Copenhagen, a browser reload of the authenticated operation-state endpoint returned the successful Start record and `desired_state: running`. The later non-packaged read-only database check found records consistent with the live observations.
- The dashboard Stop operation succeeded at 18:54:54Z (19:54:54 Copenhagen); Docker showed the single project container exited. The dashboard Start operation succeeded at 19:00:57Z (20:00:57 Copenhagen); Docker later reported the pinned container healthy and the page showed Ready.
- The current runtime is `Ready` on Baseline 8K / 4 sequences, model `RedHatAI/Qwen3.5-4B-quantized.w4a16`, pinned revision `7a613872f394578b0b52b683ff4ac47516b4bcaf`, alias `ddu-narrator`, parser `qwen3`, and pinned vLLM image `v0.32.0@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34`. Docker inspection showed `ddu_inference_bus` with alias `ddu-llm` and no host-published ports.
- **Database readback reconciled.** The earlier empty read was from the Codex package-redirected copy, not the database opened by the non-packaged diagnostic. The real database contains administrator, session, and operation records; persistence across a controller restart remains unverified.

## Automated checks

Automated checks use mocked Docker and PostgreSQL adapters. Live inference acceptance is recorded separately below; the authorized window covered Stop and Start only.

| Check | Result | Evidence |
|---|---|---|
| Repository Python suite | Passed | `dashboard/backend/.venv/Scripts/python.exe -m pytest -q` — 197 passed on the Task 15 fix working tree. |
| Dashboard backend suite | Passed | 157 backend tests are included in the 197-test repository run. |
| Backend lint | Passed | `dashboard/backend/.venv/Scripts/python.exe -m ruff check scripts tests dashboard/backend/app dashboard/backend/tests` — all checks passed. |
| Frontend tests | Passed | Node v24.15.0 / npm 11.12.1; `npm --prefix dashboard/frontend test -- --run` — 35 passed. `npm ci` reported zero vulnerabilities. |
| TypeScript | Passed | `npm --prefix dashboard/frontend run typecheck`. |
| Frontend production build | Passed | `npm --prefix dashboard/frontend run build` — TypeScript and Vite build passed. |
| Compose rendering | Passed | `docker compose --env-file .env.example config --quiet` — valid; no service was started. |
| YAML lint | Passed | `yamllint -d relaxed compose.yaml .github/workflows/ci.yml` passed on LF-normalized copies; the Windows working copies use CRLF. No YAML changed in the final fix. |
| Shell lint | Passed | ShellCheck passed for LF-normalized `scripts/check_prereqs.sh` via WSL. |
| Fresh whole-branch review | Complete | No Critical findings. The Important cancellation finding was fixed test-first; one Minor status-network observation is deferred in the SDD ledger. The tokenless setup remains a documented user-directed deviation from the original plan. |
| GitHub Actions | Passed | `contract-and-config` passed for both push and pull-request events on head `f979e24`. |
| Diff whitespace | Passed | `git diff --check` passed on the Task 15 fix working tree. |
| Windows DPAPI CurrentUser round trip | Passed in focused automated check | Recorded during Task 3 on this Windows host. Cross-user behavior remains pending below. |
| Local browser/controller session | Partial | The operator manually signed in; the operation-state endpoint returned `configured: true`, and protected Models controls displayed live status. The controller binds to `127.0.0.1:8765`; no-cookie protected status returns 401. A later non-packaged read-only check found administrator and session records in the expected database. Controller-restart persistence remains unverified. |

## Pending Windows-host acceptance

These checks require a separate operator window. Keep them pending until an operator runs and records the actual evidence. The authorized live window for this record covered Stop and Start.

| Check | Status | Evidence to record |
|---|---|---|
| Scheduled Task install, sign-in, sign-out, and uninstall | Partial | The current-user task runs from `D:\projects\Local-AI-API-LITE` with Limited run level; the operator manually signed in successfully. Sign-out and a fresh uninstall-preserves-data check remain unverified. |
| Docker Desktop unavailable and recovery | Pending | Controller remains available, inference state reports `Unknown/Unavailable`, then recovers when Docker becomes available. |
| Loopback exposure | Verified | Current `Get-NetTCPConnection` output shows only `127.0.0.1:8765`; HTTP liveness is healthy and unauthenticated operational status returns 401. |
| Stable Compose path | Configured; endpoint verified | `D:\projects\Local-AI-API-LITE` contains the configured project and `.env`; local settings readback is configured and the authenticated state endpoint returns `configured: true`. |
| Inference network and model identity | Verified after Start | Docker reports the single project container healthy on `ddu_inference_bus` with alias `ddu-llm`, no host-published ports, the pinned vLLM image/model revision, `ddu-narrator` alias, 8,192 context, and four sequences. |
| DPAPI access from another Windows user | Pending | The configured user can read its vault; a different user sees a locked vault and cannot decrypt values. |
| NVML and controller resource cost | Partial | A 15-second idle sample of the current controller on 2026-10-29 observed 0.000 CPU seconds at the process timer's reported resolution and a working set change from 30.11 to 29.90 MiB across 20 logical processors. Authenticated polling cost, startup cost, extended stability, and a current NVML sample remain unmeasured. |
| Stop, Start, slow load, failed profile, and rollback | Partial | Dashboard Stop and Start each returned success; the container exited cleanly and returned healthy. Desired state across controller restart remains unverified; the earlier database readback discrepancy was traced to package file redirection. A deliberate failed candidate/rollback and a separate slow-load scenario remain pending. |
| Optional PostgreSQL probe against an approved test database | Pending | `SELECT 1` succeeds read-only; a failed connection reports a short category and does not change inference status. |
| First administrator setup and credential reveal/browser storage | Partial | The operator previously set a password and has signed in successfully. The endpoint serves authenticated state, and a non-packaged read-only check found administrator and session records in the expected database. Persistence across controller restart remains unverified. Credential reveal/browser-storage checks remain pending. Legacy setup-token text files in `install-output` are ignored by the tokenless controller. |

## Development and live-test actions

- The current-user Scheduled Task was restarted from the stable `D:\projects\Local-AI-API-LITE` checkout to serve the updated tokenless setup code. Earlier installer runs wrote legacy setup-token files under the current user's `install-output` directory; the current controller does not read them. No token or password value is recorded here.
- Live Compose Stop and Start were performed through the authenticated dashboard as described above. No Restart, profile switch, rollback, benchmark, dashboard job, or database probe was run.
- The existing `check_server.py` made only bounded GET requests to `/health` and `/v1/models` from an ephemeral client on `ddu_inference_bus`; it sent no prompts and changed no inference state.
- No host port was exposed, and no model cache, database, or other persistent volume was removed.
- PR #3 was merged by the repository owner; Codex did not perform a deployment or publish a release.
- PR #3, #4, and #5 are merged. PR #6 is the current open follow-up; Codex did not merge, deploy, or publish a release.

## Historical local acceptance update — 2026-10-29 18:47 Copenhagen

- Administrator browser sign-in: the operator signed in manually at about 18:38 without sharing the password. The authenticated Overview showed inference Ready and protected telemetry. The Settings page later showed a current session and one other active session.
- Authentication persistence remains unresolved: a direct read-only query of `C:\Users\marcu\AppData\Local\Local-AI-API-LITE\Dashboard\metadata.sqlite3` still found zero `admin_account` rows and zero active `auth_sessions`. A no-cookie `GET /auth/state` returned `setup_required: false` and `authenticated: false`; no-cookie status, telemetry, and profiles requests returned 401. The browser's authenticated session list conflicts with the expected database counts. The live database path has not been confirmed, so do not treat the direct query as proof that the administrator account is absent or its persistence as verified.
- Controller exposure and resource sample: the only listener on port 8765 was `127.0.0.1`. During a 15-second sample with the dashboard open, the controller process group used 0.047 CPU seconds; summed working set changed from 44.1 MiB to 44.5 MiB.
- Inference read-only inspection: the dashboard showed Ready, `ddu-narrator`, and Baseline 8K / 4 sequences. ComposeRunner validation against the active worktree succeeded and found one running `vllm` service on `ddu_inference_bus` in `baseline-8k-4`. Docker inspection found one healthy project container, the `ddu-llm` alias, and zero host-published bindings.
- Stable Compose path: `D:\projects\Local-AI-API-LITE` is the fixed source checkout and contains `compose.yaml`, but no `.env`. The active container is labeled to the separate Codex worktree, which has the `.env`. The local settings file does not exist and `controls_available()` is false. An attempt to create a current-user-only `.env` copy in the stable checkout was rejected by the local execution policy with the result `blocked by policy`; no `.env` or settings file was created or changed. I did not try another route around that policy.
- No Start, Stop, Restart, profile apply, or rollback operation was run. Although the operator authorized a test window, lifecycle acceptance remains pending until the stable Compose root can be configured. The running inference container and its volumes were left unchanged.
- Current dashboard backend suite: `dashboard/backend/.venv/Scripts/python.exe -m pytest -q tests` — 156 passed in 31.99 seconds.


## Local acceptance update — 2026-10-29 20:07 Copenhagen

- The operator signed in manually; no password was shared. A fresh authenticated GET of `/api/control/v1/operations/state` returned `configured: true`.
- Stop acceptance: the dashboard recorded `desired_state: stopped`, `latest_operation.kind: stop`, `phase: succeeded`, `outcome_category: success`, started at `2026-10-29T18:54:47Z` and completed at `18:54:54Z`. Docker showed `local-ai-api-lite-vllm-1` exited with code 0. No volume or network was removed.
- Start acceptance: the dashboard recorded `desired_state: running`, `latest_operation.kind: start`, `phase: succeeded`, `outcome_category: success`, started at `2026-10-29T18:59:44Z` and completed at `19:00:57Z`. Docker returned the service container to healthy. The Models page showed Ready on Baseline 8K / 4 sequences, model `RedHatAI/Qwen3.5-4B-quantized.w4a16`, pinned revision `7a613872f394578b0b52b683ff4ac47516b4bcaf`, alias `ddu-narrator`, reasoning parser `qwen3`, and pinned image `vllm/vllm-openai:v0.32.0@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34`. Docker inspection found network `ddu_inference_bus`, alias `ddu-llm`, and no host-published port.
- The only listener on port 8765 remained `127.0.0.1`. The Scheduled Task runs as `MARCUS_COMPUTER\marcu` with Limited run level; its serving Python process has working directory `D:\projects\Local-AI-API-LITE\dashboard\backend` and `LOCALAPPDATA=C:\Users\marcu\AppData\Local`.
- **The operation endpoint and direct SQLite readback disagree.** The expected database at `C:\Users\marcu\AppData\Local\Local-AI-API-LITE\Dashboard\metadata.sqlite3` still contains `desired_state=running`, profile `baseline-8k-4`, `updated_at=1793299406.0` (18:43:26Z), no `dashboard_operations` rows, and zero `admin_account` and active `auth_sessions` rows. The live endpoint subsequently returned successful Stop and Start records with newer timestamps. The process environment, source path helper, and database path used by the shell all point to the same expected data directory, but that does not explain the response mismatch. Root cause is unconfirmed; do not treat administrator/session persistence or desired-state-across-controller-restart as verified.
- Current container is left running and healthy, matching the pre-test state. The test window did not include Restart, profile apply, failed-profile rollback, benchmarks, dashboard jobs, or database probes. Legacy setup-token files remain ignored by the current tokenless setup flow.
