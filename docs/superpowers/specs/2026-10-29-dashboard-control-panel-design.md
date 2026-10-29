# Local-AI-API-LITE dashboard completion design

## Intent

Complete the local control panel described by the PR #3 execution plan. It serves one local administrator, remains useful when inference or Docker is unavailable, and supports safe operations on the existing single vLLM Compose project. The dashboard is not a public API, model scheduler, or owner of external database data.

## Goals

- Complete the secure foundation first: one-use setup, password change, protected read-only status, sessions, local secrets storage, and controller startup independent of inference.
- Add validated Start/Stop/Restart/profile operations only after authentication and startup are complete.
- Add fixed smoke/benchmark jobs, bounded history and sanitized logs, then optional read-only PostgreSQL monitoring.
- Preserve the current OpenAI-compatible contract, `ddu-narrator`, private `ddu-llm:8080`, and one active inference container.
- Keep generated credentials, runtime configuration, SQLite data, vault keys, logs, and artifacts outside Git.
- Test lifecycle and failure behavior with fakes. Do not stop, restart, switch, benchmark, or reconfigure the live inference container during ordinary implementation.

## Non-goals and anti-goals

- No merge, release, public/LAN exposure, Tailscale setup, general shell/Compose input, model scheduler, second inference deployment, or persistent-volume deletion.
- No prompts, completions, tokens, passwords, DSNs, or full HTTP error bodies in logs, SQLite, browser storage, or benchmark artifacts.
- Do not claim Windows host acceptance until the documented operator checklist is run separately.

## Current state and constraints

- PR #3 (`6a772a9`) is open against `main` (`10e44a5`); CI passed, and there are no PR comments or submitted reviews.
- The active inference container is healthy on Docker context `desktop-linux`, uses the pinned vLLM image and Qwen revision, alias `ddu-narrator`, 8192 context / 4 sequences, parser `qwen3`, and network alias `ddu-llm`. It publishes no host ports.
- Docker labels show that the live Compose project is in a separate Codex worktree at `C:\Users\marcu\.codex\worktrees\vllm-first-service\Local-AI-API-LITE`. That worktree has a local `.env`; this PR worktree and `D:\projects\Local-AI-API-LITE` do not. The active project’s Compose config validates without output. No `.env` values were read.
- No dashboard startup task or listener on port 8765 is currently installed. The current manual controller path is loopback-only but unauthenticated.
- The selected design must not guess that the PR worktree is the live inference project. It uses an explicit, protected, local Compose-project setting; until set and validated, mutation controls report unavailable. This is a proposed default because no path-policy selection arrived during recon.

## Approaches considered

### A. User-context local controller (recommended)

Keep FastAPI and React in one local app. Use SQLite for server-side session/operation metadata, Argon2id for the admin verifier, a DPAPI-protected root key plus AES-GCM for vault values, and a reversible Task Scheduler logon task running as the Docker Desktop user. Require an explicit local Compose-project path before enabling inference mutations.

This fits the existing stack, Windows Docker Desktop’s per-user context, and the low-overhead requirement. The controller runs after user sign-in, remains separate from vLLM, and does not start inference automatically. It is not available before sign-in or after the user signs out; the docs must say so.

### B. Privileged Windows service

Run the controller before sign-in as a machine service. This offers broader availability, but Docker Desktop uses the interactive user’s context and the service would need a separate identity/privilege design. This is a poor fit for the current host and the plan’s least-privilege constraint, so it is not selected.

### C. External identity or secrets service

Delegate authentication and secrets to a remote IdP or Vault. This adds network/runtime dependencies and weakens local-first availability. It also exceeds the requested small personal controller, so it is not selected.

## Proposed design

### Local runtime and data

- Keep the controller on the Windows host, separate from the inference Compose project, bound to `127.0.0.1` only.
- Provide explicit install/uninstall PowerShell scripts. Installation builds/verifies prerequisites, issues a one-use bootstrap credential for local console display, and registers a user-context logon task with restart-on-failure. It never starts vLLM.
- Store state under `%LOCALAPPDATA%\Local-AI-API-LITE\Dashboard`: SQLite metadata/history, the DPAPI-wrapped vault key, bootstrap/recovery state, settings, and bounded artifacts. Add these paths to Git ignore rules if any are placed under the checkout.
- The local settings file contains a Compose project root chosen by the operator/installer, not supplied to an API. Validate its Compose project identity, required network/cache settings, and expected service before permitting an operation. If missing or changed, expose controls as unavailable.

### Authentication and protected reads

- A local CLI creates a cryptographically random, single-use setup token and stores only a verifier in SQLite. The CLI displays it in its interactive local output; the web API never returns it. A local CLI recovery command revokes sessions and issues a new token.
- Setup accepts that token and requires the administrator to choose the password before creating the single account. Store an Argon2id verifier only.
- Store random opaque session-token hashes server-side in SQLite. Use HttpOnly, SameSite=Lax cookies, Secure on HTTPS, bounded expiry, revocation, and new identifiers on login. Keep CSRF values in server session state and in frontend memory only.
- Validate Host and Origin for login/setup/recovery and every mutation. Require a CSRF token for authenticated mutations. Rate-limit login attempts persistently and return generic failures.
- Keep only the minimal liveness endpoint and setup-state endpoint public. Protect status, telemetry, profiles, settings, jobs, logs, database, and credential metadata routes. Do not add permissive CORS.

### Local secrets foundation

- Generate a random root key locally and protect it with Windows DPAPI CurrentUser. Keep the protected key file separate from SQLite.
- Use a vetted AES-GCM implementation for credential values; store encrypted values separately from credential metadata. Never fall back to plaintext if DPAPI or decryption fails.
- Tests inject a fake protector on non-Windows CI. No external credentials are generated or provisioned unless a later integration explicitly needs them and the operator authorizes it.

### Status and timeout model

- Keep current read-only Docker/telemetry semantics and make the public liveness response minimal.
- Use one bounded Docker refresh worker and an in-memory last-known snapshot. A request returns the current snapshot promptly and may schedule one refresh; additional requests never create additional workers. Mark snapshots stale with their observation time and expose the latest poll failure separately from the last successful observation.
- Bound each subprocess and the whole refresh. Since HTTP returns the cache promptly, give the frontend timeout margin above the API response budget; a timed-out refresh cannot accumulate or outlive the single worker slot.
- Docker unavailable remains `Unknown/Unavailable`; a successful query with no matching container remains `Stopped`. Slow startup remains `Starting/Loading` for a configurable, multi-minute window rather than becoming Failed after a generic HTTP timeout.

### Interface

- Keep the existing React/TypeScript/Vite UI and simple CSS. Add Overview, Models, Settings first, then Databases, Benchmarks, and Logs as their actual behavior lands.
- Require login/setup before any operational UI or API data. Keep the session and CSRF token in memory; never use localStorage for credentials, sessions, or telemetry.
- Show stale/unavailable/loading/error explicitly, preserve device-wide GPU labeling and separate successful-observation timestamps, and keep status requests single-flight.
- Do not render a control until its backend works. Incomplete later sections may be explicitly marked unavailable.
- Standardize on Node `>=22.12` in package metadata, both READMEs, and CI while Vitest 5 is in use.

### Safe inference operations

- Persist desired profile/state and operation journal in SQLite outside the repo. Enforce one writer across controller instances.
- Accept only fixed action names and IDs from the validated profile registry. Resolve all Compose paths and flags from protected local settings and repository-owned profile data; no arbitrary command, argument, path, image, model, port, or environment-key input from API requests.
- Update the existing project’s effective `.env` atomically, preserving unrelated settings and restrictive permissions, so ordinary Compose CLI calls see the same selected profile. Preflight the expected project/network/cache and unpublished port before mutation.
- Persist a deliberate Stop before issuing the fixed `docker compose stop vllm` operation. Never auto-start vLLM after controller restart. Start only after the old container releases `ddu-llm`; report Loading until health and the existing bounded contract check pass.
- Commit a profile as current only after health and smoke checks. On failure, restore the last known-good profile, verify recovery, preserve all volumes, and report candidate and rollback outcomes.
- Use fake Docker/Compose adapters for all automated state-machine tests. Never invoke mutations against the active container during implementation.

### Jobs, history, logs, and databases

- Wrap only the existing readiness, synthetic smoke, and benchmark scripts with fixed job types and bounded numeric choices. Run a single mutating/benchmark job at a time. Persist bounded provenance/state in SQLite WAL and sanitized JSONL outside the checkout; do not retain request/response bodies.
- Expose only bounded sanitized controller/inference log tails; redact credentials, environment values, auth headers, and paths before display/export.
- Add PostgreSQL only as an optional read-only `SELECT 1` monitor with explicit timeouts. Distinguish unconfigured from unavailable, keep DSNs in the vault, and isolate failures from inference and controller status. Never create schemas or write database data.

## Delivery order and review boundaries

1. **Step A / Milestone 1:** auth/bootstrap, sessions, vault foundation, explicit user-context startup/install path, bounded cached status, protected Overview/Models/Settings, Node runtime contract, docs and security tests. Do not add inference mutations until this step is complete.
2. **Step B / Milestone 2:** explicit Compose source-of-truth configuration, desired state, single-writer operation journal, fixed Start/Stop/Restart/profile operations, health/contract validation and rollback tests.
3. **Step C / Milestone 3:** fixed jobs, bounded history/artifacts, sanitized Logs, comparison and tests.
4. **Step D / Milestone 4:** optional read-only PostgreSQL monitor and tests.

Keep each step in a separate commit on the existing PR branch and update the runbook and acceptance record. Run automated checks after each step. Keep PR #3 open and do not merge or publish. The final operator acceptance record must remain pending for any manual Windows/Docker/GPU checks not executed; do not stop or reconfigure the current inference service to complete it.

## Acceptance evidence

- Automated tests prove bootstrap uniqueness/use-once behavior, auth/session/CSRF/host boundaries, vault encryption/locked behavior, bounded status refresh and truthful failure states, fake-operation lock/rollback behavior, bounded sanitized jobs/logs, and PostgreSQL isolation.
- CI runs backend tests and lint, frontend tests/typecheck/build on Node 24, Compose config validation, and all checks without model downloads/GPU.
- Manual checklist records the controller’s logon/sign-out behavior, loopback binding, Docker Desktop unavailable behavior, real stopped/loading inference status, network/port contract, NVML labeling, and measured resource cost. No results from remote CI may substitute for this evidence.
