# Dashboard acceptance record

**Record date:** 2026-10-29
**Branch:** `codex/setup-auth-error-feedback`
**Pull requests:** [PR #3](https://github.com/MarcusFunt/Local-AI-API-LITE/pull/3), merged as `4946530`; [PR #4](https://github.com/MarcusFunt/Local-AI-API-LITE/pull/4), merged as `66a3c1f`; [PR #5](https://github.com/MarcusFunt/Local-AI-API-LITE/pull/5), open for authentication and tokenless first-run setup follow-ups.

## Automated checks

The implementation checks used mocked Docker and PostgreSQL adapters. No inference container was stopped, restarted, reconfigured, or benchmarked during these checks.

| Check | Result | Evidence |
|---|---|---|
| Repository Python suite | Passed | `dashboard/backend/.venv/Scripts/python.exe -m pytest -q` — 196 passed on `77217e0`. |
| Dashboard backend suite | Passed | `dashboard/backend/.venv/Scripts/python.exe -m pytest -q dashboard/backend/tests` — 156 passed on `77217e0`. |
| Backend lint | Passed | `dashboard/backend/.venv/Scripts/python.exe -m ruff check scripts tests dashboard/backend/app dashboard/backend/tests`. |
| Frontend tests | Passed | `npm --prefix dashboard/frontend run test -- --run` — 35 passed. |
| TypeScript | Passed | `npm --prefix dashboard/frontend run typecheck`. |
| Frontend production build | Passed | `npm --prefix dashboard/frontend run build`. |
| Compose rendering | Passed | `docker compose --env-file .env.example config --quiet` — valid; no service was started. |
| YAML lint | Passed | `yamllint -d relaxed compose.yaml .github/workflows/ci.yml` against LF-normalized source; this Windows checkout stores working copies with CRLF. |
| Shell lint | Passed | ShellCheck passed for `scripts/check_prereqs.sh` via WSL after normalizing the Windows checkout's CRLF line endings. |
| Fresh whole-branch review | Pending for PR #5 | PR #5 has no review decision or review comments yet. Earlier PR #3/#4 review findings are addressed in the merged baseline. |
| GitHub Actions | Passed | Both `contract-and-config` checks passed on PR #5 head `77217e0`. |
| Diff whitespace | Passed | `git diff --check` passed before commit `77217e0`. |
| Windows DPAPI CurrentUser round trip | Passed in focused automated check | Recorded during Task 3 on this Windows host. Cross-user behavior remains pending below. |
| Local browser/controller session | Partial | The operator manually signed in; authenticated Overview rendered protected status and telemetry, and Settings displayed current and other server-reported sessions. The controller binds to `127.0.0.1:8765`; no-cookie protected status returns 401. The expected per-user `metadata.sqlite3` still has zero administrator and active-session rows, so the live auth persistence path remains unreconciled. |

## Pending Windows-host acceptance

These checks require a separate operator window. Keep them pending until an operator runs and records the actual evidence. The live service must remain unchanged during ordinary code review and CI.

| Check | Status | Evidence to record |
|---|---|---|
| Scheduled Task install, sign-in, sign-out, and uninstall | Partial | The current-user task runs from `D:\projects\Local-AI-API-LITE` with Limited run level and is currently running. Sign-in/sign-out and a fresh uninstall-preserves-data check remain unverified. |
| Docker Desktop unavailable and recovery | Pending | Controller remains available, inference state reports `Unknown/Unavailable`, then recovers when Docker becomes available. |
| Loopback exposure | Verified | Current `Get-NetTCPConnection` output shows only `127.0.0.1:8765`; HTTP liveness is healthy and unauthenticated operational status returns 401. |
| Stable Compose path | Configured; dashboard verification pending | A current-user-only `.env` was copied into `D:\projects\Local-AI-API-LITE`; `LocalSettingsStore.controls_available()` returns true and the dashboard configuration command validated the rendered Compose config. The authenticated Models page still reports controls unavailable, so lifecycle actions remain unrun pending reconciliation. |
| Inference network and model identity | Verified | Read-only Docker inspection on 2026-10-29 found one healthy `local-ai-api-lite` `vllm` container using the pinned `v0.32.0` image, on `ddu_inference_bus` with alias `ddu-llm`, no host-published ports, and the pinned model revision, `ddu-narrator` alias, 8,192 context, and four sequences. No inference state was changed. |
| DPAPI access from another Windows user | Pending | The configured user can read its vault; a different user sees a locked vault and cannot decrypt values. |
| NVML and controller resource cost | Partial | A 15-second idle sample of the current controller on 2026-10-29 observed 0.000 CPU seconds at the process timer's reported resolution and a working set change from 30.11 to 29.90 MiB across 20 logical processors. Authenticated polling cost, startup cost, extended stability, and a current NVML sample remain unmeasured. |
| Stop, slow load, failed profile, and rollback | Pending | Desired Stop persists across controller restart; long load remains active; failed candidate restores the verified last-known-good profile without deleting volumes. |
| Optional PostgreSQL probe against an approved test database | Pending | `SELECT 1` succeeds read-only; a failed connection reports a short category and does not change inference status. |
| First administrator setup and credential reveal/browser storage | Partial | The password-only setup page was served; the operator chose a password and subsequently signed in successfully. The authenticated Settings page lists sessions, but the expected metadata database has zero administrator/session rows, so the runtime auth database path remains unconfirmed. Credential reveal and browser-storage checks remain pending. Four legacy setup-token text files remain in `install-output`; the current controller ignores them. |

## Development actions not performed

- The current-user Scheduled Task was restarted from the stable `D:\projects\Local-AI-API-LITE` checkout to serve the updated tokenless setup code. Earlier installer runs wrote legacy setup-token files under the current user's `install-output` directory; the current controller does not read them. No token or password value is recorded here.
- No live Compose Start, Stop, Restart, profile switch, benchmark, dashboard job, or database probe was run.
- The existing `check_server.py` made only bounded GET requests to `/health` and `/v1/models` from an ephemeral client on `ddu_inference_bus`; it sent no prompts and changed no inference state.
- No host port was exposed, and no model cache, database, or other persistent volume was removed.
- PR #3 was merged by the repository owner; Codex did not perform a deployment or publish a release.
- The task-identity fix is a post-merge follow-up in open PR #4; PR #3 remains merged by the repository owner.

## Local acceptance update — 2026-10-29 18:47 Copenhagen

- Administrator browser sign-in: the operator signed in manually at about 18:38 without sharing the password. The authenticated Overview showed inference Ready and protected telemetry. The Settings page later showed a current session and one other active session.
- Authentication persistence remains unresolved: a direct read-only query of `C:\Users\marcu\AppData\Local\Local-AI-API-LITE\Dashboard\metadata.sqlite3` still found zero `admin_account` rows and zero active `auth_sessions`. A no-cookie `GET /auth/state` returned `setup_required: false` and `authenticated: false`; no-cookie status, telemetry, and profiles requests returned 401. The browser's authenticated session list conflicts with the expected database counts. The live database path has not been confirmed, so do not treat the direct query as proof that the administrator account is absent or its persistence as verified.
- Controller exposure and resource sample: the only listener on port 8765 was `127.0.0.1`. During a 15-second sample with the dashboard open, the controller process group used 0.047 CPU seconds; summed working set changed from 44.1 MiB to 44.5 MiB.
- Inference read-only inspection: the dashboard showed Ready, `ddu-narrator`, and Baseline 8K / 4 sequences. ComposeRunner validation against the active worktree succeeded and found one running `vllm` service on `ddu_inference_bus` in `baseline-8k-4`. Docker inspection found one healthy project container, the `ddu-llm` alias, and zero host-published bindings.
- Stable Compose path: `D:\projects\Local-AI-API-LITE` is the fixed source checkout and contains `compose.yaml`, but no `.env`. The active container is labeled to the separate Codex worktree, which has the `.env`. The local settings file does not exist and `controls_available()` is false. An attempt to create a current-user-only `.env` copy in the stable checkout was rejected by the local execution policy with the result `blocked by policy`; no `.env` or settings file was created or changed. I did not try another route around that policy.
- No Start, Stop, Restart, profile apply, or rollback operation was run. Although the operator authorized a test window, lifecycle acceptance remains pending until the stable Compose root can be configured. The running inference container and its volumes were left unchanged.
- Current dashboard backend suite: `dashboard/backend/.venv/Scripts/python.exe -m pytest -q tests` — 156 passed in 31.99 seconds.

## Local Compose path setup — 2026-10-29

- Created `D:\projects\Local-AI-API-LITE\.env` by copying the active Compose environment from the separate service worktree after confirming both `compose.yaml` files match exactly after line-ending normalization. The copied file's SHA-256 matched its source; its ACL grants full control only to the current Windows user. `.env` remains ignored by Git, and no values are recorded here.
- Configured the dashboard's protected per-user `settings.json` to use `D:\projects\Local-AI-API-LITE`. `LocalSettingsStore.controls_available()` reads true, and `python -m app.settings configure-compose-root --path D:\projects\Local-AI-API-LITE` completed the Compose render/allowlist validation.
- Restarted only the `Local-AI-API-LITE Dashboard Controller` scheduled task (listener PID 45628 → 60120); it returned to `Running` on `127.0.0.1:8765`. Inference stayed healthy and was not changed.
- After a fresh dashboard sign-in, the Models page still said controls were unavailable and showed desired state `Stopped` while inference was `Ready`. The local settings readback and the authenticated UI therefore disagree. No inference lifecycle action was issued; reconcile the live operation-state response before attempting Start, Stop, profile, or rollback checks.
