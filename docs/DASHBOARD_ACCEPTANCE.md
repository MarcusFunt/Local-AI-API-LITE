# Dashboard acceptance record

**Record date:** 2026-10-29
**Branch:** `codex/fix-dashboard-task-identity` (post-merge follow-up)
**Pull requests:** [PR #3](https://github.com/MarcusFunt/Local-AI-API-LITE/pull/3), merged by the repository owner as `4946530`; [PR #4](https://github.com/MarcusFunt/Local-AI-API-LITE/pull/4), open for the task-identity follow-up.

## Automated checks

The implementation checks used mocked Docker and PostgreSQL adapters. No inference container was stopped, restarted, reconfigured, or benchmarked during these checks.

| Check | Result | Evidence |
|---|---|---|
| Repository Python suite | Passed | `python -m pytest -q` — 185 passed on 2026-10-29 on the local follow-up branch. |
| Dashboard backend suite | Passed | `python -m pytest -q dashboard/backend/tests` — 145 passed on 2026-10-29 on the local follow-up branch. |
| Backend lint | Passed | `python -m ruff check scripts tests dashboard/backend/app dashboard/backend/tests` — passed on the local follow-up branch. |
| Frontend tests | Passed | `npm --prefix dashboard/frontend test` — 22 passed. |
| TypeScript | Passed | `npm --prefix dashboard/frontend run typecheck`. |
| Frontend production build | Passed | `npm --prefix dashboard/frontend run build`. |
| Compose rendering | Passed | `docker compose --env-file .env.example config --quiet`; no service was started. |
| YAML lint | Passed | `yamllint -d relaxed compose.yaml .github/workflows/ci.yml` against LF-normalized source; this Windows checkout stores working copies with CRLF. |
| Shell lint | Passed | ShellCheck passed for `scripts/check_prereqs.sh` via WSL after normalizing the Windows checkout's CRLF line endings. |
| Fresh whole-branch review | Passed for PR #3; follow-up reviewed with no Critical/Important findings | The Compose validation gap, DSN validation echo, and vault-metadata completeness finding were addressed with regression tests and schema migration 7. Follow-up PR #4's GitHub Actions checks passed on `9522918`. One Minor is deferred: add an automated negative assertion for a different task principal; the helper's rejection of `NT AUTHORITY\SYSTEM` was checked manually. |
| Diff whitespace | Passed | `git diff --check` on the local follow-up changes. |
| Windows DPAPI CurrentUser round trip | Passed in focused automated check | Recorded during Task 3 on this Windows host. Cross-user behavior remains pending below. |
| Local browser/controller session | Partial | The current-user task launches the controller, its liveness endpoint is healthy, setup is required, and an unauthenticated status request returns 401. Administrator setup/login remains pending. |

## Pending Windows-host acceptance

These checks require a separate operator window. Keep them pending until an operator runs and records the actual evidence. The live service must remain unchanged during ordinary code review and CI.

| Check | Status | Evidence to record |
|---|---|---|
| Scheduled Task install, sign-in, sign-out, and uninstall | Partial | Installed, uninstalled, and reinstalled as the current user with Limited run level. Uninstall removed the task/listener while preserving metadata, jobs/artifacts directories, and the token file. The current task runs from the stable `D:\projects\Local-AI-API-LITE` checkout. Sign-in/sign-out behavior remains unverified. |
| Docker Desktop unavailable and recovery | Pending | Controller remains available, inference state reports `Unknown/Unavailable`, then recovers when Docker becomes available. |
| Loopback exposure | Verified | `netstat` showed the controller listening on `127.0.0.1:8765`; the HTTP liveness and protected status endpoints responded locally. |
| Stable Compose path | Pending | The stable checkout has no `.env`; no Compose root was configured and no credentials were copied. Lifecycle controls therefore remain unavailable. |
| Inference network and model identity | Partial | Read-only Docker inspection showed the healthy pinned vLLM image digest on `ddu_inference_bus`, the `ddu-llm` alias, and no published ports. Served model revision and `ddu-narrator` response identity still need the operator check. |
| DPAPI access from another Windows user | Pending | The configured user can read its vault; a different user sees a locked vault and cannot decrypt values. |
| NVML and controller resource cost | Partial | `nvidia-smi` reported device-wide RTX 3060 telemetry (2% utilization, 420/12288 MiB, 37°C, 8.94 W). A 15-second idle sample of the controller listener process measured 0.016% CPU and 47.7 MiB working set. Startup cost and authenticated polling overhead remain unmeasured. |
| Stop, slow load, failed profile, and rollback | Pending | Desired Stop persists across controller restart; long load remains active; failed candidate restores the verified last-known-good profile without deleting volumes. |
| Optional PostgreSQL probe against an approved test database | Pending | `SELECT 1` succeeds read-only; a failed connection reports a short category and does not change inference status. |
| First administrator setup and credential reveal/browser storage | Pending | A fresh one-time setup token is stored at `%LOCALAPPDATA%\Local-AI-API-LITE\Dashboard\install-output\setup-token.txt` with a current-user-only ACL. For this install, console output was redirected to a protected local log, the token was moved to this file, and raw-token lines were removed from installer logs. The administrator must choose a password in the local UI; reveal and browser-storage behavior remain untested. |

## Development actions not performed

- The current-user Scheduled Task is installed from the stable `D:\projects\Local-AI-API-LITE` checkout. Installer output was captured locally; the setup token was moved to the current-user-only file above, and raw-token installer logs were sanitized. The token value is not recorded here.
- No live Compose Start, Stop, Restart, profile switch, job, or database probe was run.
- No host port was exposed, and no model cache, database, or other persistent volume was removed.
- PR #3 was merged by the repository owner; Codex did not perform a deployment or publish a release.
- The task-identity fix is a post-merge follow-up in open PR #4; PR #3 remains merged by the repository owner.
