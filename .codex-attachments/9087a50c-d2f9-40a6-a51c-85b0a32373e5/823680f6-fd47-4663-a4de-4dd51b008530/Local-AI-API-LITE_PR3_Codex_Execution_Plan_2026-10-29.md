# Local-AI-API-LITE: PR #3 Completion and Product Alignment Plan

**Prepared:** 2026-10-29<br>
**Repository:** MarcusFunt/Local-AI-API-LITE<br>
**Current implementation branch:** codex/feat-dashboard-readonly-react-control-panel<br>
**Branch reviewed:** 6a772a9f27a298605463b127e146e68eafc2f0cc<br>
**Current pull request:** #3, “feat: add read-only local dashboard”<br>
**Purpose:** Give Codex a concrete, staged implementation plan that turns the current dashboard prototype into the small, dependable local control panel requested for Local-AI-API-LITE.

---

## 1. Instruction to Codex

Start by refreshing the repository state. The branch SHA and PR state above describe the review snapshot and may have moved. Continue from the existing dashboard branch and pull request if they still exist; do not recreate already completed work. Compare with the current main branch before editing.

Implement the milestones below in order. Keep each milestone reviewable, update documentation as behavior changes, and do not leave placeholder controls that look functional but have no working backend. Preserve the existing vLLM service contract and the project’s local-first, low-overhead goals.

Do not merge the pull request, publish a release, expose a port to the LAN or Internet, or stop/reconfigure the user’s running inference service as part of ordinary development. Use mocked service adapters in automated tests. Real-host acceptance that stops or restarts inference must be a separately documented operator action.

If a requirement conflicts with an existing implementation detail, inspect the current repository and record the chosen compatible design before changing behavior. Do not silently weaken a requirement to make the PR easier to merge.

---

## 2. What the user is building

Local-AI-API-LITE is the user’s independent, local inference service and its small operations dashboard. It is not the history-game application, a public API, a broad AI-stack orchestrator, or a high-throughput model-serving platform.

The dashboard should let one administrator:

1. See whether the dashboard controller, inference service, host, GPU, and configured databases are available.
2. Start, stop, restart, and safely switch the single active inference service between explicitly validated profiles.
3. Run bounded, predefined protocol checks and benchmarks, then review and compare their history.
4. Inspect useful, sanitized operational logs.
5. Set up the administrator account once, change the initial password immediately, and manage credentials for services that the dashboard actually controls.

The dashboard must remain available when vLLM is stopped, loading, unhealthy, or absent. It should start automatically for the local operator, use little CPU and RAM, and favor clear status and recovery over visual polish.

The user normally expects one or two concurrent requests. Long model loading and inference are acceptable when they allow a better or larger model. Do not optimize this project around maximum throughput, simultaneous model residency, or a general model scheduler. Keep the interface and profile system ready for future model experiments, including slower or CPU-offloaded candidates, while requiring each candidate to pass validation before it can be enabled.

---

## 3. Repository and PR #3 baseline

The reviewed PR adds a React/Vite frontend, a FastAPI controller, Docker and host telemetry, validated-profile display, polling, documentation, and automated checks. The PR reported 59 Python tests plus frontend test/build and Compose validation; the GitHub Actions checks were green in the review snapshot.

The PR is useful as a read-only prototype, but it does not yet deliver the current product requirements:

| Gap | Current PR behavior | Required outcome |
|---|---|---|
| Administrator access | Status, telemetry, and profile endpoints have no authentication | First-run setup, a single admin account, mandatory initial password change, protected API routes |
| Generated credentials | No secure secrets-store setup or credential-management foundation | Unique setup credential; encrypted local vault foundation; generated/rotated credentials only for supported managed services |
| Controller availability | Runbook starts Uvicorn manually in a terminal | Controller starts independently of vLLM when the local operator signs in and can report inference as stopped or unavailable |
| Status timing | Frontend request timeout is 4.5 seconds; up to three Docker CLI operations can run sequentially, each with its own 3-second timeout | A defined total status deadline or a fast cached snapshot; the UI must not report a false outage while a bounded Docker query is still running |
| Node documentation | Runbook allows Node 20.19 | Document and declare a Node version supported by the selected Vitest version |
| Actual host behavior | CI uses mocks/Linux and cannot establish Windows, WSL2, Docker Desktop, or NVML behavior | Manual Windows-host acceptance before calling the dashboard milestone complete |
| Requested operations | No inference controls, database monitor, benchmark history, or logs yet | Add these in ordered milestones below; do not misrepresent the read-only prototype as the complete dashboard |

### Relevant current project baseline

Reconfirm these values in the current repository before relying on them:

- Windows 11, WSL2, Docker Desktop, NVIDIA RTX 3060 12 GB, 32 GB system RAM.
- One vLLM inference service, one model resident at a time, OpenAI-compatible API.
- Private Docker network: ddu_inference_bus.
- Stable service alias: ddu-llm; API model alias: ddu-narrator.
- No host-published inference port.
- Existing validated Qwen3.5-4B W4A16 profiles: 4096 context / 2 sequences and 8192 context / 4 sequences.
- Existing contract, smoke-test, and benchmark scripts should be reused where they fit.
- A recorded host run took about 149 seconds to start from cache and about 245 seconds in a cold-cache case. Treat multi-minute loading as normal. Never label a model failed merely because it has not loaded within a short generic HTTP timeout.

These are baseline facts to verify, not permission to alter the current profile, stop the current container, or download a new model during implementation.

---

## 4. Non-negotiable invariants

1. Preserve the existing OpenAI-compatible API shape, ddu-narrator model alias, ddu-llm network alias, and trusted-client path.
2. Never publish the unauthenticated inference API on a host port.
3. Never run two inference containers that claim ddu-llm at the same time.
4. Do not delete model-cache, database, or other persistent volumes during a profile switch or recovery.
5. Do not put prompts, completions, credentials, API keys, or full HTTP error bodies in logs, audit events, SQLite, benchmark artifacts, or browser storage.
6. Do not execute arbitrary user-supplied shell commands, Docker arguments, Compose files, model IDs, image references, paths, or environment-variable names.
7. Keep external PostgreSQL data and the DDU game’s prompts, cases, RAG, rules, and application credentials outside this repository’s ownership.
8. A deliberate operator Stop must remain stopped after a controller restart. The controller must not silently start inference again.
9. Unknown, missing, or failed telemetry must display as unavailable/unknown, never as zero or healthy.
10. Keep the dashboard process and its history/vault separate from the inference container and its lifecycle.
11. Keep dependencies and runtime work small. Use React/TypeScript/Vite, FastAPI, plain CSS or similarly light styling, SQLite only for local dashboard metadata/history, and polling initially. Do not add Redis, Kubernetes, a message broker, a multi-service architecture, or a large UI framework.
12. Keep remote access disabled by default. Only consider Tailscale access after authentication, CSRF protections, host checks, and local acceptance pass.

---

## 5. Milestone 0 — Rebase and verify the working baseline

Before coding:

1. Fetch current main and the dashboard branch; inspect the current diff and PR comments/checks.
2. Check whether any of the reviewed work has since merged or changed. Do not repeat merged work.
3. Inspect README, Compose configuration, .env behavior, model tests, profile data, and the dashboard implementation plan.
4. Record the current inference configuration and service state using read-only inspection only.
5. Confirm the controller’s intended Windows execution context and how that context reaches Docker Desktop. Keep the existing Compose project as the only inference deployment.
6. Identify tracked and untracked runtime paths. Keep .env secrets, SQLite files, vault keys, bootstrap tokens, logs, and benchmark outputs outside tracked source.

**Exit condition:** Codex can state the current branch/base, active runtime contract, profile source of truth, and Windows-to-Docker access path without changing the running service.

---

## 6. Milestone 1 — Complete the secure, always-available dashboard foundation

This is the required completion of the current PR #3 scope. Complete it before adding inference mutation routes.

### 6.1 First-run administrator setup

Implement a one-time setup flow:

- Generate a unique, unpredictable, single-use temporary setup credential with the operating system’s cryptographically secure random generator.
- Do not ship admin/admin, a shared default password, or a fixed bootstrap token.
- Display the setup credential only to the local installer through a protected local installation output or file with restrictive permissions. Do not return it from a public web endpoint or put it in normal logs.
- The first successful setup must require the administrator to choose a new password. Invalidate the temporary credential immediately after successful setup.
- Store only a strong password verifier, such as Argon2id output. Do not store a reversibly encrypted copy of the administrator password.
- If the setup state is incomplete, make the UI show a clear locked/setup state. Do not expose telemetry or operational routes before setup is complete.
- Provide a local, operator-authorized recovery path. Do not create an anonymous network password-reset endpoint.

### 6.2 Sessions and protected routes

- Use server-side sessions; do not store session tokens, passwords, or service credentials in localStorage.
- Set HttpOnly and SameSite cookie attributes. Set Secure when using HTTPS.
- Rotate session identifiers after login; support logout, session expiry, and server-side revocation.
- Add login rate limits, generic failed-login responses, CSRF protection on every mutation, and Origin/Host validation.
- Protect status, telemetry, profile, database, job, log, settings, and secret APIs with the admin session. The static login/setup assets may be available before login.
- Keep any unauthenticated liveness endpoint minimal: it may indicate controller process health but must not reveal host, model, database, or credential data.
- Bind to loopback by default. Do not add permissive CORS.

### 6.3 Secrets foundation

Create a small local secrets subsystem without building a general-purpose Vault product:

- Keep an OS-protected root key separate from the SQLite metadata store. On Windows, use a supported OS key-protection mechanism such as DPAPI; do not invent cryptography.
- Use a vetted authenticated-encryption library and standard authenticated encryption for secret values.
- Store metadata separately from values: credential name, owning integration, purpose, created/rotated timestamps, and state.
- Generate secret values on the server. Show a generated value only when needed for secure handoff, and only through an authenticated one-time reveal flow. Never include it in logs, error text, analytics, or audit data.
- Do not claim an external credential was rotated or revoked until the target service confirms the operation.
- Do not pretend to manage credentials for services the dashboard does not control. For a database that LITE can only monitor, either use an existing read-only account supplied through a protected setup flow or generate/provision a scoped account only when the database administrator explicitly authorizes provisioning.
- If the vault key is missing or cannot be decrypted, show a locked/unavailable state. Never fall back to plaintext.

### 6.4 Independent controller startup

The controller must have a supported local installation/startup path separate from inference:

- For the current Windows + Docker Desktop setup, prefer a Windows-hosted user-context launcher or logon-triggered Scheduled Task so the controller does not depend on the vLLM container or WSL staying alive.
- Start the dashboard process automatically for the local operator and configure restart-on-failure. Use the same Windows account/context that has the intended Docker Desktop access.
- The dashboard should still serve login, settings, and status when Docker Desktop is unavailable. Report inference as Unknown/Unavailable and preserve the cause; do not report it as stopped.
- Start the controller without automatically starting vLLM.
- Bind the web server to 127.0.0.1 and make installation/uninstallation explicit and reversible.
- Document the exact lifecycle behavior: Windows boot, user sign-in, user sign-out, Docker Desktop unavailable, WSL stopped, and inference stopped. If true operation before sign-in or across sign-out is not supported by the chosen Docker context, state that precisely rather than claiming unconditional always-on service.
- Do not run the dashboard inside the inference Compose project.

If a Windows service or scheduled task would require broad privileges, do not silently grant them. Keep the smallest viable local permission model and document any Docker privilege boundary. A future Tailscale path must not be enabled as part of this milestone.

### 6.5 Read-only status and telemetry

Retain the current overview and model-profile display, improving only what is needed for reliability and accurate state:

- Show controller state separately from inference state.
- Keep states distinct: Ready, Stopped, Starting/Loading, Switching, Degraded, Failed, Unknown.
- Preserve the last successful observation timestamp separately from the latest failed poll.
- Clearly label GPU telemetry as device-wide and explain that other GPU processes affect VRAM figures.
- Show absent NVML or Docker as unavailable. Do not convert missing readings to zero.
- Keep only bounded browser-memory sparkline samples; do not persist telemetry unless a later feature needs it.
- Keep status fetching single-flight. Do not start a second poll while a previous one is still active.

### 6.6 Resolve the status-timeout mismatch

Current code gives the frontend a 4.5-second request timeout, while a normal backend status read may issue docker ps, docker inspect, and docker stats sequentially, each with a 3-second per-command timeout. Correct the design rather than merely increasing one arbitrary timeout:

1. Define a total backend request budget and a frontend budget that exceeds it with margin.
2. Ensure every subprocess call has a timeout and every request has a total deadline.
3. Prefer returning a cached last-known snapshot quickly, with observation time and stale status, if Docker Desktop queries can exceed the UI budget.
4. A frontend timeout must not leave an unbounded backend request or create accumulating work.
5. Show a stale/slow Docker status distinctly from controller failure.

Choose the simplest implementation that meets the above. Do not introduce a message broker or a high-frequency background sampler.

### 6.7 Fix the JavaScript runtime contract

- Update dashboard/README.md and docs/DASHBOARD.md to require Node 22.12 or newer while Vitest 5 is used.
- Declare the supported runtime in package.json using an engines field and, if useful, a repository Node-version file.
- Keep CI on a supported Node release.
- Ensure the runbook’s install instructions, Vite/Vitest versions, and CI agree.

### 6.8 Phase 1 interface

Keep the compact React interface, with these destinations:

- Overview: controller, inference, CPU/RAM/GPU, last success, current issue.
- Models: active configuration and validated/experimental profile states.
- Settings: administrator password, sessions, controller startup, vault/credential metadata.

Do not add nonfunctional buttons. Database, Benchmarks, and Logs may be clearly marked as not implemented until their milestones are complete; do not show fake data or imply that they are working.

**Milestone 1 exit:** A fresh local install creates a unique one-time setup credential, forces password change, protects status routes, launches the controller independently of inference, and shows accurate read-only status with inference stopped or Docker unavailable.

---

## 7. Milestone 2 — Safe inference controls and validated profiles

Implement working controls only after Milestone 1 authentication and startup behavior are complete.

### 7.1 Configuration source of truth

Before adding profile-apply code, decide how dashboard-selected profiles correspond to the existing Compose/.env workflow. Keep one authoritative effective configuration:

- Do not duplicate the Compose project or create a second vLLM deployment.
- Keep profile values in a versioned, repository-owned allowlist.
- Persist the desired profile and desired inference state outside tracked source.
- If a generated env file or updated .env is needed, write atomically, restrict permissions, preserve unrelated configuration, and document how ordinary CLI commands see the same active configuration.
- Never accept arbitrary image references, model IDs, model revisions, command flags, mounts, ports, or Compose files from an API request.
- Keep the current validated 4K/2 and 8K/4 profiles as the only enabled profiles until a new profile completes real-host validation.
- Allow future candidate profiles to be recorded as Experimental/Disabled with immutable model revision and image digest, but never permit one-click deployment until explicitly validated.

### 7.2 Operations and state machine

Implement Start, Stop, Restart, and Apply Profile with a single-writer operation lock and durable operation record.

Suggested phases:

1. Validate session, CSRF token, current state, requested profile ID, and operation conflict.
2. Run preflight: profile is allowlisted; Compose configuration resolves; required network/cache path is present; inference service identity is the expected one; no unexpected host port is published.
3. Show the profile/configuration difference and warn that active requests may be interrupted.
4. Persist requested desired state and operation journal before mutating the service.
5. Gracefully stop the existing single service when required. Never start the replacement before the old service releases ddu-llm.
6. Start the allowlisted Compose service with the selected values.
7. Show a normal Loading/Starting state during model initialization. Use a configurable timeout informed by measured cold/warm starts; a conservative default around ten minutes is acceptable until host evidence justifies another value.
8. Check Docker health, then run the existing bounded protocol/contract smoke check over the private network.
9. Commit the new selected profile only after checks pass. Otherwise restore the last known-good profile, verify recovery, and report both candidate and rollback outcomes.

Required behavior:

- Stop releases model VRAM but leaves the dashboard, auth, secrets metadata, database monitor, and history available.
- A deliberate Stop remains stopped after dashboard or computer restart; do not auto-resurrect inference.
- If the controller restarts during an operation, reconcile the journal with actual Docker state and show whether recovery is needed.
- Do not claim an operation succeeded just because Docker accepted a start command; require health and contract checks.
- Do not delete volumes as part of restart, profile apply, failure recovery, or cleanup.
- Use explicit confirmation for disruptive operations; keep confirmation concise and specific.
- A switch or benchmark is a single active operation. Reject or clearly queue conflicting actions; do not run them concurrently.

### 7.3 Maintain the serving contract

- Preserve ddu-llm:8080 for trusted containers and the ddu-narrator API alias.
- Keep the inference network private and host port unpublished.
- Use existing status, readiness, and synthetic contract tests where appropriate.
- Keep the first version one active model at a time. Long loading is acceptable; reliable rollback and correct status matter more than swap speed.
- Do not promote CPU-offloaded or larger-model experiments automatically. Record their profile and measurements separately from the known-good baseline.

**Milestone 2 exit:** The operator can stop the service, leave the dashboard available, start the last selected profile, switch only between approved profiles, recover from failed startup, and retain the API/network contract.

---

## 8. Milestone 3 — Tests, benchmarks, results, and logs

### 8.1 Predefined jobs only

Wrap the existing scripts as fixed job types. Do not offer a shell box or arbitrary command input.

Initial jobs:

- Server readiness and served-model identity check.
- Synthetic API contract/smoke check.
- Bounded performance benchmark.
- Future model-evaluation job only after a fixed, versioned evaluation set is agreed and its privacy/output handling is documented.

Validate all arguments against fixed choices and numeric limits. Default performance runs should prioritize one or two concurrent requests, because that matches expected use. A higher-concurrency profile can remain an explicit diagnostic option; it must not become the default optimization target.

Record enough provenance to compare results responsibly: profile ID/revision, engine image digest, host/GPU, script version, concurrency, request count, duration, success/error counts, latency percentiles, and measured GPU/host telemetry if available.

Do not record prompt or response content. Reuse the existing sanitized benchmark clients and artifact format where possible.

### 8.2 Job lifecycle and history

- Run at most one service-mutating or benchmark job at a time.
- Store job metadata in a small local SQLite database using WAL mode.
- Store sanitized JSONL artifacts outside the repository in a dedicated dashboard data directory.
- Record queued/running/completed/failed/cancelled states, start/end times, progress where available, exit category, and artifact location.
- Bound output size and retention. Provide explicit cleanup with a dry-run or clear confirmation.
- Compare runs only when key conditions match; highlight differences in model revision, engine, context, sequences, hardware, or test version.
- Use polling for status first. Add SSE only if measured progress-display needs justify it.

### 8.3 Logs

- Add a bounded, read-only Logs view for controller and inference diagnostics.
- Limit to a small configurable tail and avoid storing unbounded copies.
- Redact tokens, passwords, authorization headers, environment values, and sensitive paths before display/export.
- Do not collect request bodies, prompts, completion text, or private error bodies.
- Log operation metadata and result categories, never secret values.

**Milestone 3 exit:** The operator can launch a predefined smoke test or benchmark, see the job state, review a sanitized result, and compare compatible runs without manually using a terminal.

---

## 9. Milestone 4 — Optional PostgreSQL health monitoring

Add a Databases view for configured external PostgreSQL instances. The dashboard monitors availability; it does not own database data or schema.

- Unconfigured must differ from unavailable.
- Use a read-only connection and a minimal bounded probe such as SELECT 1.
- Use explicit connection and query timeouts; failures must not block the controller, inference, or other dashboard pages.
- Store DSNs/passwords only through the protected secret path; redact them from logs, status responses, and UI errors.
- Display connection state, last check, response time, and a short sanitized failure category. Do not expose full DSNs.
- Do not create databases, modify schemas, run migrations, or write application data.
- Provision or rotate a scoped monitoring account only if the administrator explicitly authorizes it and the database confirms the action. Otherwise accept an existing read-only monitor credential through protected setup.
- The application database for the history game remains outside LITE’s control.

**Milestone 4 exit:** A missing or down database is clearly reported and does not affect inference or dashboard availability. A configured healthy database displays a recent successful probe without exposing its secret.

---

## 10. User interface and overhead requirements

Keep the UI simple and operational:

- Main sections: Overview, Models, Databases, Benchmarks, Logs, Settings.
- Use plain React state and a small API client; add no global state library unless a real need appears.
- Use plain CSS and small inline graphs; do not spend effort on elaborate animations or charts.
- Make state text explicit; color cannot be the only status signal.
- Make unavailable, stale, loading, and failed states visually distinct.
- Poll status every five seconds while visible and back off while hidden; keep one request in flight. Use slower database/log refresh rates.
- Do not poll metrics at sensor-loop rates. The dashboard is not a telemetry acquisition system.
- Measure idle CPU/RAM and active polling cost on the actual host; document the observed values rather than promising a target without measurement.

---

## 11. Security, privacy, and recovery acceptance

Codex must implement automated coverage for behavior that can be tested without the user’s GPU. Do not run tests that stop or reconfigure the real inference service. Use fake Docker/Compose adapters for lifecycle and rollback tests.

Required automated checks:

- Fresh install emits a unique setup credential; no fixed/shared bootstrap secret.
- Bootstrap credential is one-use; password change is mandatory; status endpoints return unauthorized before setup/login.
- Session cookie flags, logout/revocation, CSRF rejection, origin validation, login rate limiting, and generic failure messages.
- Secret values are absent from API responses, logs, audit rows, exceptions, and benchmark outputs except the explicit one-time reveal.
- Missing vault key or corrupt encrypted value produces a locked state, not plaintext fallback.
- Docker unavailable, container stopped, loading, healthy, unhealthy, exited, and malformed inspect responses map to truthful states.
- Slow/timeout Docker queries remain bounded; frontend shows stale/unavailable without starting duplicate requests.
- Profile allowlist rejects unknown or modified values before any Docker mutation.
- Single-writer behavior rejects conflicts; successful apply commits only after health/smoke; failed apply attempts rollback and reports the outcome.
- Deliberate Stop persists after controller restart.
- PostgreSQL absent/down/slow/healthy states do not break unrelated status.
- Benchmark jobs enforce allowed arguments, timeout, cancellation, bounded artifacts, and redaction.
- Frontend build, typecheck, and tests run on a supported Node release; backend checks and Compose config validation stay usable without model downloads or GPU.

Manual Windows-host acceptance checklist:

1. Install/start the controller from the documented local path and verify it binds only to loopback.
2. Confirm the dashboard appears after the documented Windows sign-in/startup trigger.
3. With inference deliberately stopped, verify dashboard login, status, settings, and history remain available and GPU usage is not misreported as model usage.
4. With Docker Desktop unavailable, verify the dashboard remains responsive and reports inference as Unknown/Unavailable.
5. Start inference only in an explicitly authorized acceptance window; verify multi-minute loading remains Loading and does not produce a false failure.
6. Confirm the expected model alias and private network contract; confirm no host-published inference port.
7. Confirm NVML unavailable is shown as unavailable and device-wide VRAM is labeled correctly.
8. Measure idle controller CPU/RAM and polling overhead; check memory and sample history remain bounded.
9. If remote access is later considered, run a separate security review before enabling it. Do not use public Internet exposure.

Do not claim these manual checks passed based only on GitHub-hosted CI.

---

## 12. Recommended repository organization

Keep the existing dashboard folder. Add only the modules needed for working features, with clear boundaries, for example:

~~~text
dashboard/
  backend/
    app/
      api.py
      auth.py
      bootstrap.py
      compose_runner.py
      control_state.py
      databases.py
      docker_status.py
      jobs.py
      logs.py
      persistence.py
      profiles.py
      secrets_store.py
      telemetry.py
    tests/
  config/
    profiles.yaml
  frontend/
    src/
      pages or small page components
      api.ts
      auth state and forms
  scripts/
    install_dashboard.ps1
    uninstall_dashboard.ps1
  data/  # runtime only; ignored by Git
docs/
  DASHBOARD.md
  DASHBOARD_SECURITY.md
  DASHBOARD_ACCEPTANCE.md
~~~

Do not add modules just to match this example. Keep the shipped dependency list minimal. All runtime data, generated keys, auth state, job history, logs, and artifacts must remain outside tracked source and survive inference stop/restart.

---

## 13. Documentation updates

Update README and the dashboard runbook so a new operator can:

1. Install the dashboard and understand its startup trigger.
2. Complete first-run setup and change the temporary password.
3. Log in, log out, and recover administrator access locally.
4. Understand that the dashboard is separate from inference and does not start vLLM on its own.
5. Start/stop/switch only supported profiles and understand the loading and rollback states.
6. Configure a read-only external PostgreSQL monitor safely.
7. Start predefined checks/benchmarks and find their sanitized results.
8. Understand data locations, retention, backup, and recovery behavior.
9. Know precisely which actions need Docker access and why the dashboard must remain loopback-only by default.
10. Use the same supported Node version locally and in CI.

Clearly distinguish implemented features from future work. Remove statements that call a partial prototype the complete Phase 1 implementation.

---

## 14. Suggested implementation order and review boundaries

Keep each step coherent and independently reviewable:

### Step A — PR #3 foundation

Complete authentication/bootstrap, vault foundation, controller autostart, timeout handling, runtime-version docs, and protected read-only Overview/Models/Settings. No inference mutation routes yet.

### Step B — Inference operation layer

Implement allowlisted profile selection, desired-state persistence, single-writer operation journal, start/stop/restart, health/contract validation, rollback, and recovery UI.

### Step C — Jobs and operational history

Implement predefined checks/benchmarks, single-job execution, SQLite history, sanitized artifacts, and Logs.

### Step D — Database monitoring

Add optional read-only PostgreSQL health checks and protected credential setup.

After each step, update its docs and acceptance record. Do not merge or deploy automatically. Leave final merge/release decisions to the repository owner.

---

## 15. Final definition of done

The overall dashboard is complete when all of these are true:

- It is a small React control panel that starts independently of inference and is available through the documented local startup path.
- The first administrator credential is unique and one-use, and a password change is required before normal access.
- Operational APIs require authenticated sessions and mutations are protected against CSRF.
- The local vault never falls back to plaintext and records no secret values in logs or history.
- The operator can inspect status when inference is stopped, loading, unhealthy, or Docker is unavailable.
- The operator can start/stop/restart and switch only between validated profiles, with a durable desired state and verified rollback.
- No second model container competes for the stable network alias, and the inference API remains private.
- Predefined tests and benchmarks can be launched and reviewed without exposing arbitrary shell execution or request content.
- Compatible results can be compared; logs and artifacts are bounded and sanitized.
- Optional database health monitoring is read-only and cannot take down the dashboard or inference.
- Automated checks pass on supported runtimes, and manual Windows-host acceptance is recorded separately.
- The dashboard’s actual resource cost and startup behavior are measured on the target machine.

The goal is a reliable personal control panel for experimenting with local inference. Keep it easy to operate, safe to recover, and open to future model profiles without turning it into a general-purpose AI platform.
