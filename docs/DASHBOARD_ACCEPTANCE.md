# Dashboard acceptance record

**Record date:** 2026-10-29
**Branch:** `codex/feat-dashboard-readonly-react-control-panel`
**Pull request:** [PR #3](https://github.com/MarcusFunt/Local-AI-API-LITE/pull/3)

## Automated checks

The implementation checks used mocked Docker and PostgreSQL adapters. No inference container was stopped, restarted, reconfigured, or benchmarked during these checks.

| Check | Result | Evidence |
|---|---|---|
| Repository Python suite | Passed | `python -m pytest -q` — 180 passed on 2026-10-29. |
| Dashboard backend suite | Passed | `python -m pytest -q dashboard/backend/tests` — 140 passed on 2026-10-29. |
| Backend lint | Passed | `python -m ruff check scripts tests dashboard/backend/app dashboard/backend/tests`. |
| Frontend tests | Passed | `npm --prefix dashboard/frontend test` — 22 passed. |
| TypeScript | Passed | `npm --prefix dashboard/frontend run typecheck`. |
| Frontend production build | Passed | `npm --prefix dashboard/frontend run build`. |
| Compose rendering | Passed | `docker compose --env-file .env.example config --quiet`; no service was started. |
| YAML lint | Passed | `yamllint -d relaxed compose.yaml .github/workflows/ci.yml` against LF-normalized source; this Windows checkout stores working copies with CRLF. |
| Shell lint | Passed | ShellCheck passed for `scripts/check_prereqs.sh` via WSL after normalizing the Windows checkout's CRLF line endings. |
| Fresh whole-branch review | Passed | No Critical findings; the Important Compose validation gap and DSN validation echo were fixed with regression tests. One Minor vault-metadata completeness follow-up is documented in [DASHBOARD_SECURITY.md](DASHBOARD_SECURITY.md). |
| Diff whitespace | Passed | `git diff --check`. |
| Windows DPAPI CurrentUser round trip | Passed in focused automated check | Recorded during Task 3 on this Windows host. Cross-user behavior remains pending below. |
| Local browser/controller session | Not run | The dashboard Scheduled Task was not installed or launched. |

## Pending Windows-host acceptance

These checks require a separate operator window. Keep them pending until an operator runs and records the actual evidence. The live service must remain unchanged during ordinary code review and CI.

| Check | Status | Evidence to record |
|---|---|---|
| Scheduled Task install, sign-in, sign-out, and uninstall | Pending | Standard-user account, task principal and trigger, controller availability before/after sign-out, and confirmation that runtime data survives uninstall. |
| Docker Desktop unavailable and recovery | Pending | Controller remains available, inference state reports `Unknown/Unavailable`, then recovers when Docker becomes available. |
| Loopback exposure | Pending | Listener is bound only to `127.0.0.1:8765`; no remote listener is reachable. |
| Stable Compose path | Pending | Explicit operator-selected stable checkout is stored in local settings; a temporary worktree is not used as the long-term path. |
| Inference network and model identity | Pending | `ddu-llm` remains on `ddu_inference_bus`, no host-published port, expected pinned image digest, Qwen revision, and `ddu-narrator` alias. |
| DPAPI access from another Windows user | Pending | The configured user can read its vault; a different user sees a locked vault and cannot decrypt values. |
| NVML and controller resource cost | Pending | Device-wide telemetry availability, controller CPU/RAM, and expected normal-workload cost. |
| Stop, slow load, failed profile, and rollback | Pending | Desired Stop persists across controller restart; long load remains active; failed candidate restores the verified last-known-good profile without deleting volumes. |
| Optional PostgreSQL probe against an approved test database | Pending | `SELECT 1` succeeds read-only; a failed connection reports a short category and does not change inference status. |
| Credential reveal and browser storage | Pending | Reveal requires an authenticated CSRF-protected action, response is `no-store`, and the value is absent from browser storage and list responses. |

## Development actions not performed

- The Scheduled Task was not installed, and no bootstrap token was issued by this implementation run.
- No live Compose Start, Stop, Restart, profile switch, job, or database probe was run.
- No host port was exposed, and no model cache, database, or other persistent volume was removed.
- No PR merge, deployment, or publication was performed.
