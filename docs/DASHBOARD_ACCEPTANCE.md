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
| Local browser/controller session | Partial | The current-user task runs from the stable checkout; the controller health endpoint is healthy, it binds to `127.0.0.1:8765`, and unauthenticated status returns 401. The operator reports submitting a first administrator password and `/auth/state` reports setup is no longer required, but a direct read-only query of the expected per-user `metadata.sqlite3` found no administrator row. Reconcile the live auth database before recording setup persistence or login as passed. |

## Pending Windows-host acceptance

These checks require a separate operator window. Keep them pending until an operator runs and records the actual evidence. The live service must remain unchanged during ordinary code review and CI.

| Check | Status | Evidence to record |
|---|---|---|
| Scheduled Task install, sign-in, sign-out, and uninstall | Partial | The current-user task runs from `D:\projects\Local-AI-API-LITE` with Limited run level and is currently running. Sign-in/sign-out and a fresh uninstall-preserves-data check remain unverified. |
| Docker Desktop unavailable and recovery | Pending | Controller remains available, inference state reports `Unknown/Unavailable`, then recovers when Docker becomes available. |
| Loopback exposure | Verified | Current `Get-NetTCPConnection` output shows only `127.0.0.1:8765`; HTTP liveness is healthy and unauthenticated operational status returns 401. |
| Stable Compose path | Pending | The stable checkout has no `.env`, and `LocalSettingsStore.controls_available()` is false. Lifecycle controls remain unavailable until the operator explicitly configures a stable project root. |
| Inference network and model identity | Verified | Read-only Docker inspection on 2026-10-29 found one healthy `local-ai-api-lite` `vllm` container using the pinned `v0.32.0` image, on `ddu_inference_bus` with alias `ddu-llm`, no host-published ports, and the pinned model revision, `ddu-narrator` alias, 8,192 context, and four sequences. No inference state was changed. |
| DPAPI access from another Windows user | Pending | The configured user can read its vault; a different user sees a locked vault and cannot decrypt values. |
| NVML and controller resource cost | Partial | A 15-second idle sample of the current controller on 2026-10-29 observed 0.000 CPU seconds at the process timer's reported resolution and a working set change from 30.11 to 29.90 MiB across 20 logical processors. Authenticated polling cost, startup cost, extended stability, and a current NVML sample remain unmeasured. |
| Stop, slow load, failed profile, and rollback | Pending | Desired Stop persists across controller restart; long load remains active; failed candidate restores the verified last-known-good profile without deleting volumes. |
| Optional PostgreSQL probe against an approved test database | Pending | `SELECT 1` succeeds read-only; a failed connection reports a short category and does not change inference status. |
| First administrator setup and credential reveal/browser storage | Partial | The updated browser serves a password-only setup form, the operator reports choosing a password, and `/auth/state` reports setup complete. The expected metadata database still shows zero admin rows, so persisted setup/login is not verified. Credential reveal and browser-storage checks remain pending. Four legacy setup-token text files remain in `install-output`; the current controller ignores them. |

## Development actions not performed

- The current-user Scheduled Task was restarted from the stable `D:\projects\Local-AI-API-LITE` checkout to serve the updated tokenless setup code. Earlier installer runs wrote legacy setup-token files under the current user's `install-output` directory; the current controller does not read them. No token or password value is recorded here.
- No live Compose Start, Stop, Restart, profile switch, benchmark, dashboard job, or database probe was run.
- The existing `check_server.py` made only bounded GET requests to `/health` and `/v1/models` from an ephemeral client on `ddu_inference_bus`; it sent no prompts and changed no inference state.
- No host port was exposed, and no model cache, database, or other persistent volume was removed.
- PR #3 was merged by the repository owner; Codex did not perform a deployment or publish a release.
- The task-identity fix is a post-merge follow-up in open PR #4; PR #3 remains merged by the repository owner.
