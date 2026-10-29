# Dashboard security review

**Review date:** 2026-10-29
**Scope:** dashboard controller, dashboard API and UI, local persistence, Docker operations/jobs/logs, and optional PostgreSQL health probes in PR #3.

## Security boundary

The controller runs under the signed-in Windows user that can access Docker Desktop and binds to `127.0.0.1:8765`. The model API remains on the private `ddu_inference_bus` network with no host-published port. The dashboard does not expose a Docker socket or enable remote access. Tailscale and other remote access remain out of scope until a separate authenticated remote-access design and host acceptance exist.

The controller does not start inference at launch. Desired state is persisted independently; restart reconciliation is read-only, and a deliberate Stop remains stopped. Mutations require a configured stable Compose path in local settings, an administrator session, CSRF validation, Host/Origin checks, and an explicit UI confirmation where applicable.

## Authentication and browser boundary

- Initial setup and recovery use one-time local-console tokens. Passwords use Argon2id; opaque token and session values are stored as digests.
- The browser receives an HttpOnly, SameSite session cookie. CSRF state stays in memory. The API applies login throttling and generic authentication failures.
- `/auth/state` and minimal controller liveness are the public information surface. Operational status, profiles, operations, jobs, logs, and database routes require an administrator session. Mutations require the CSRF token.
- OpenAPI and interactive documentation routes are disabled. The frontend makes same-origin requests and does not store passwords, setup tokens, session identifiers, CSRF values, or database connection strings in browser storage.
- Request validation failures return a generic 422 response; they do not echo rejected input such as an oversized database connection string.

## Local storage and secrets

- SQLite metadata uses WAL and lives in the per-user dashboard data directory. Job and database probe rows contain bounded metadata and safe categories, not credentials or DSNs.
- Vault metadata stores the credential name, owning integration, purpose, creation and rotation timestamps, and a state field separately from encrypted values; new credentials are marked active.
- Vault values use AES-GCM with a random root key protected by Windows DPAPI CurrentUser. Missing or corrupt key material locks the vault; there is no plaintext fallback.
- Database connection strings are encrypted vault values with the `database-monitor` purpose. List, update, check, and remove responses omit them. An administrator can request an explicit one-time reveal; the response is `no-store`, and the UI holds the value only in component memory.
- Runtime data remains outside the checkout. Uninstall preserves metadata and vault files.

## Docker lifecycle, jobs, and logs

- Compose source selection is explicit and local. The runner fixes the project name, Compose files, service, image, network, alias, mounts, and profile allowlist; it validates configuration and runtime labels before mutation.
- Inference operations use a shared SQLite activity gate and journal. The runner has fixed Start and Stop operations and does not run `compose down`, remove containers or volumes, publish host ports, or accept arbitrary arguments.
- Jobs are limited to server readiness, the existing synthetic API smoke cases, and performance benchmark. They use fixed image/argument arrays, a read-only scripts mount, bounded resources, cancellation, capped output, per-job artifacts, and bounded retention. Prompts, completions, credentials, and full HTTP error bodies are not persisted.
- The Logs page queries only the fixed Compose service with a maximum tail of 200 lines. It reads container environment entries in memory for credential-value filtering, redacts credential patterns and paths, and suppresses content-bearing prompt/completion/message/request-body output before response or export. It does not write log copies.

## PostgreSQL monitor

The optional monitor obtains a saved connection string from the vault only for its monitoring purpose. It uses one bounded worker slot, connect and statement timeouts, a read-only connection/transaction, and the fixed query `SELECT 1`. It does not provision users, create schemas, or accept a DSN in probe requests. Failures become short categories and do not gate dashboard status or inference.

## Verification and limits

Automated coverage exercises session/CSRF protection, Host/Origin checks, DPAPI and AES-GCM behavior, Compose validation and rollback, job bounds, log redaction, database isolation, and fixed routes through fake Docker/database adapters. CI runs on Linux and does not download models or contact the live inference service. Windows scheduled-task behavior, current-user ACL behavior, other-user DPAPI rejection, actual Docker Desktop loss/recovery, NVML cost, and live stopped/loading/rollback behavior require the separate operator acceptance record in [DASHBOARD_ACCEPTANCE.md](DASHBOARD_ACCEPTANCE.md).

The controller is intended for a trusted local Windows account. A user who can access that account and its processes can use the same DPAPI identity and Docker privileges. Log filtering is a defensive projection of the fixed service output; the controller does not change Docker's underlying log retention or host-level access controls.

## Fresh review outcomes

A fresh read-only whole-branch review on 2026-10-29 found no Critical findings. The Important Compose-validation finding was addressed by restricting both the source and rendered project to the fixed service, settings, and one of the validated inference commands; regression tests reject added `privileged`/`devices` options, host ports, and altered commands. The review's DSN-validation echo finding was addressed with a generic request-validation response and a regression test. The Minor metadata-completeness finding was addressed in schema migration 7: vault metadata now records credential state and a rotation timestamp, while renames preserve the original rotation time.
