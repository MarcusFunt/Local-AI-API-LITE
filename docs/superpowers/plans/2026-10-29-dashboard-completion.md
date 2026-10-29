# Local-AI-API-LITE Dashboard Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Each task ends with a focused test cycle and commit.

**Goal:** Complete PR #3 into a secure, local-first dashboard with protected status, safe inference lifecycle controls, bounded jobs/history/logs, and optional read-only PostgreSQL monitoring.

**Architecture:** Keep one loopback-bound FastAPI process and the existing React/Vite frontend, launched in the Docker Desktop user’s Windows logon session. Use SQLite for server-side auth/session/operation/job metadata, Argon2id for the admin verifier, and a DPAPI-protected root key with AES-GCM for vault values. Read status through one bounded refresh worker and a stale-aware in-memory snapshot; require an explicit local Compose project path before any mutation is available.

**Tech Stack:** Python 3.11+, FastAPI, SQLite WAL, Argon2id, `cryptography` AES-GCM with Windows DPAPI CurrentUser, React/TypeScript/Vite, Vitest 5 on Node >=22.12, Docker CLI with fixed argument arrays, and a small PostgreSQL client for optional monitoring.

**Spec:** `docs/superpowers/specs/2026-10-29-dashboard-control-panel-design.md`; source requirements: `.codex-attachments/9087a50c-d2f9-40a6-a51c-85b0a32373e5/823680f6-fd47-4663-a4de-4dd51b008530/Local-AI-API-LITE_PR3_Codex_Execution_Plan_2026-10-29.md`.

## Global Constraints

- Preserve the existing OpenAI-compatible API shape, ddu-narrator model alias, ddu-llm network alias, and trusted-client path.
- Never publish the unauthenticated inference API on a host port.
- Never run two inference containers that claim ddu-llm at the same time.
- Do not delete model-cache, database, or other persistent volumes during a profile switch or recovery.
- Do not put prompts, completions, credentials, API keys, or full HTTP error bodies in logs, audit events, SQLite, benchmark artifacts, or browser storage.
- Do not execute arbitrary user-supplied shell commands, Docker arguments, Compose files, model IDs, image references, paths, or environment-variable names.
- Keep external PostgreSQL data and the DDU game’s prompts, cases, RAG, rules, and application credentials outside this repository’s ownership.
- A deliberate operator Stop must remain stopped after a controller restart. The controller must not silently start inference again.
- Unknown, missing, or failed telemetry must display as unavailable/unknown, never as zero or healthy.
- Keep the dashboard process and its history/vault separate from the inference container and its lifecycle.
- Keep dependencies and runtime work small. Use React/TypeScript/Vite, FastAPI, plain CSS or similarly light styling, SQLite only for local dashboard metadata/history, and polling initially. Do not add Redis, Kubernetes, a message broker, a multi-service architecture, or a large UI framework.
- Keep remote access disabled by default. Only consider Tailscale access after authentication, CSRF protections, host checks, and local acceptance pass.
- The dashboard controller binds to 127.0.0.1; model loading may take multiple minutes and a short HTTP timeout must not label it failed.
- Use a one-time setup credential, Argon2id password verifier, server-side sessions, HttpOnly/SameSite cookies, Secure on HTTPS, login rate limits, CSRF protection on mutations, and Host/Origin validation.
- Require the first administrator to set a permanent password before any operational route or dashboard data is available.
- Protect the Windows vault root key with DPAPI CurrentUser; use vetted authenticated encryption for values; never fall back to plaintext.
- Start the controller at user sign-in in the same Windows account/context that can access Docker Desktop; it does not start vLLM and is not available before sign-in or after sign-out.
- Poll status every five seconds while visible and back off while hidden; keep one request in flight.
- Use a configurable inference start timeout informed by measured cold/warm starts; a conservative default around ten minutes is acceptable until host evidence justifies another value.
- For PostgreSQL monitoring use a read-only connection and a minimal bounded probe such as SELECT 1; failures must not block controller status or inference.
- Require Node 22.12 or newer while Vitest 5 is used; keep CI on a supported Node release.
- Do not merge, deploy, or publish automatically. Manual Windows-host acceptance remains separate from GitHub-hosted CI.
- Use mocked service adapters in automated tests; do not stop or reconfigure the running inference service during development.

## Review Focus

- **No Docker context or stopped inference:** controller/login remains available, inference reports `Unknown/Unavailable` when Docker cannot be queried, and a successful empty query reports `Stopped`; cover in Tasks 4 and 5.
- **Slow or hung Docker CLI:** a single refresh worker, per-command timeout, total deadline, stale timestamp, and no accumulating work; cover in Task 4.
- **Bootstrap/session/CSRF abuse:** token reuse, login floods, wrong Host/Origin, missing CSRF, revoked/expired cookie, and generic auth failures are denied without leaking secrets; cover in Tasks 2 and 3.
- **Corrupt/missing DPAPI or vault data:** return a locked/unavailable result and never plaintext fallback; cover in Task 3.
- **Changed or malformed Compose path/profile/config:** refuse before any mutation and preserve unrelated `.env` lines and values; cover in Tasks 7 and 8.

## File Responsibilities

- `paths.py` and `persistence.py`: user-scoped runtime location, SQLite WAL/schema migrations, and cross-process metadata transactions.
- `auth.py`, `routes/auth.py`, and `bootstrap.py`: one-admin bootstrap, password/session/CSRF/rate-limit behavior, and local-only token issue/recovery CLI.
- `secrets_store.py`: DPAPI key protection, AES-GCM values, separate metadata, and authenticated one-time reveal.
- `status_cache.py` and `docker_status.py`: bounded single-worker observations, stale snapshot/failure timestamps, and truthful state mapping.
- `configuration.py`, `compose_runner.py`, `control_state.py`: protected local Compose-root configuration, fixed CLI calls, durable desired state, shared activity gate, operation journal, and rollback.
- `jobs.py`, `logs.py`, `databases.py`: fixed bounded jobs, sanitized log views, and isolated read-only PostgreSQL probes.
- `routes/` modules: authenticated API boundaries; page components under `frontend/src/pages/` own the user-visible feature behavior.
- `dashboard/scripts/`: explicit reversible Windows installation/uninstallation only; runtime data remains outside the checkout.

---

## Step A — Secure, always-available dashboard foundation

### Task 1: Runtime paths and SQLite metadata foundation

**Files:** Create `dashboard/backend/app/paths.py`, `dashboard/backend/app/persistence.py`, and `dashboard/backend/tests/test_paths.py`, `dashboard/backend/tests/test_persistence.py`; modify `.gitignore` and `dashboard/backend/pyproject.toml`.

**Interfaces:** `data_directory() -> Path` resolves a user-scoped runtime path; `ensure_private_directory(path: Path) -> None` applies user-scoped permissions; `MetadataStore(path: Path).transaction()` yields a SQLite connection in a transaction and raises only a sanitized `MetadataStoreError` for storage failures.

- [ ] Write tests `test_data_directory_uses_local_app_data_on_windows`, `test_runtime_data_is_outside_repository`, `test_runtime_directory_is_private_to_current_user`, `test_metadata_store_creates_wal_schema`, `test_schema_upgrade_is_idempotent`, and `test_database_failure_is_reported_without_path_or_sql_leak`.
- [ ] Run those tests and verify they fail before implementation.
- [ ] Implement the Windows `%LOCALAPPDATA%` path with a documented XDG fallback for tests/dev; add ignored runtime paths only if they can live under the checkout.
- [ ] Create schema/version infrastructure with WAL and foreign keys; do not store raw setup tokens, session IDs, passwords, or secret values in this task.
- [ ] Run the focused tests; verify they pass.
- [ ] Run Ruff on the backend modules.
- [ ] Commit as `feat: add dashboard local persistence foundation`.

### Task 2: One-time bootstrap, administrator auth, sessions, and protected APIs

**Files:** Create `dashboard/backend/app/auth.py`, `dashboard/backend/app/routes/__init__.py`, `dashboard/backend/app/routes/auth.py`, `dashboard/backend/tests/test_auth.py`, and `dashboard/backend/tests/test_auth_routes.py`; modify `dashboard/backend/app/api.py`, `dashboard/backend/app/persistence.py`, `dashboard/backend/pyproject.toml`, `dashboard/backend/tests/conftest.py`, and `dashboard/backend/tests/test_api.py`.

**Interfaces:** `MetadataStore.migrate(version, statements)` applies ordered, idempotent schema upgrades. `AuthService(store, clock, token_factory, password_hasher, limiter)` implements `issue_bootstrap()`, `issue_recovery()`, `setup(token, password)`, `login(password, client_key)`, `load_session(cookie)`, `list_sessions(session)`, `revoke_other_sessions(session)`, `logout(session)`, `change_password(session, old_password, new_password)`, and `recover_with_local_token(token, new_password)`. A FastAPI dependency `require_session(request)` protects operational routes; `require_csrf(request, session)` protects authenticated mutations. Use an 8-hour session lifetime and rate-limit to 5 failed logins per client in 15 minutes.

- [ ] Write tests `test_bootstrap_token_is_unique_and_single_use`, `test_setup_requires_new_password_and_creates_argon2id_verifier`, `test_invalid_bootstrap_token_does_not_hash_candidate_password`, `test_login_rotates_session_and_sets_http_only_same_site_cookie`, `test_secure_cookie_is_set_for_https`, `test_session_token_is_hashed_in_sqlite`, `test_expired_and_revoked_sessions_are_rejected`, `test_logout_revokes_server_session`, `test_password_change_revokes_old_sessions`, `test_login_rate_limit_survives_app_restart`, `test_auth_failures_are_generic`, `test_wrong_host_or_origin_is_rejected`, `test_wrong_origin_is_rejected_for_loopback_host`, `test_missing_or_wrong_csrf_is_rejected`, `test_recovery_token_is_one_use_and_revokes_sessions`, and `test_invalid_recovery_token_does_not_hash_candidate_password`.
- [ ] Write route tests proving `/auth/state` and minimal liveness are the only unauthenticated information routes; the existing status, telemetry, and profiles routes return 401 before login. Require later settings, jobs, logs, and database routers to use the same session/CSRF dependencies as they are added in their tasks. Add session-list and revoke-other-sessions tests that reveal metadata only, and verify `/openapi.json`, `/docs`, and `/redoc` are disabled.
- [ ] Run tests to verify failure before implementation.
- [ ] Implement a single administrator account, Argon2id verifier, SHA-256 digests for opaque one-use setup/recovery and session tokens, server-side expiry/revocation, persisted login throttling, CSRF token state, and generic errors. Add `/auth/sessions` and `/auth/sessions/revoke-others` for metadata-only session management.
- [ ] Keep the public information surface to `/auth/state` and minimal liveness; setup/login/recovery remain token-protected and all other routes require a session.
- [ ] Keep setup/recovery secrets out of web responses except the user-submitted token; no password/session/CSRF storage in localStorage.
- [ ] Run focused auth tests; verify they pass.
- [ ] Run the full backend suite and Ruff.
- [ ] Commit as `feat: protect dashboard with local admin sessions`.

### Task 3: DPAPI-backed local vault foundation

**Files:** Create `dashboard/backend/app/secrets_store.py`, `dashboard/backend/tests/test_secrets_store.py`, and `dashboard/backend/tests/fakes.py`; modify `dashboard/backend/app/persistence.py` and `dashboard/backend/pyproject.toml`.

**Interfaces:** `KeyProtector.protect(bytes) -> bytes` and `unprotect(bytes) -> bytes`; `WindowsDpapiProtector` uses DPAPI CurrentUser only; `SecretsStore(store, key_file, protector, cipher)` expects Task 2's metadata schema v2 and adds vault migration v3. It implements `put(name, integration, purpose, value) -> str`, `metadata()`, `get_for_service(secret_id, purpose) -> str` for authenticated internal adapters only, `issue_reveal(secret_id) -> str`, `consume_reveal(reveal_token) -> str`, and `lock_state()`.

- [x] Write tests `test_secret_value_is_authenticated_encrypted_and_metadata_separate`, `test_tampered_ciphertext_is_locked`, `test_missing_dpapi_key_is_locked_without_plaintext_fallback`, `test_secret_metadata_never_contains_value`, `test_reveal_token_is_one_use_and_stored_as_digest`, `test_non_windows_tests_use_fake_key_protector`, and a Windows-only real DPAPI CurrentUser round-trip test; initialize Task 2's v2 schema before vault migration v3.
- [x] Verify the tests fail before implementation.
- [x] Add a vetted AES-GCM dependency; store only a DPAPI-protected random root key in a separate file and encrypted values in a values table distinct from metadata. On unsupported platform or decryption error, fail closed.
- [x] Run focused tests with the fake protector on CI and the real DPAPI protector on Windows.
- [x] Run the backend suite and Ruff.
- [x] Commit as `feat: add DPAPI-protected dashboard vault`.

### Task 4: Bounded cached status and read-only API protection

**Files:** Create `dashboard/backend/app/status_cache.py`, `dashboard/backend/tests/test_status_cache.py`, and update `dashboard/backend/tests/test_api.py`; modify `dashboard/backend/app/docker_status.py`, `dashboard/backend/app/api.py`, and frontend API types/tests as needed.

**Interfaces:** `StatusCache(reader, clock, max_age, command_budget)` returns a snapshot with `observed_at`, `stale`, `refreshing`, and last poll error; `get_snapshot() -> dict` never waits for a Docker refresh and permits one worker only.

- [x] Write tests `test_first_request_returns_unknown_without_blocking_on_slow_docker`, `test_refresh_runs_only_once_for_concurrent_requests`, `test_stale_last_known_snapshot_returns_while_refresh_runs`, `test_slow_subprocess_obeys_per_command_and_total_deadline`, `test_failed_refresh_preserves_last_success_and_updates_failure`, and `test_no_data_is_unknown_not_zero`.
- [x] Add `test_successful_empty_container_listing_is_stopped` and `test_unhealthy_exited_and_malformed_inspect_states_are_truthful`; preserve the existing distinction between Docker query failure (`Unknown/Unavailable`) and a successful query finding no inference service (`Stopped`).
- [x] Verify those tests fail before implementation.
- [x] Add a single refresh worker with a bounded queue/slot, per-command timeouts, a total monotonic deadline, stale observation fields, and sanitized failure categories. The API response comes from memory immediately; no unbounded thread or background sensor loop.
- [x] Protect status, telemetry, and profile reads with `require_session`; keep liveness minimal; include controller/inference separation and stale/slow status in the response.
- [x] Set the frontend request timeout above the API’s response budget and preserve one in-flight poll.
- [x] Run backend and frontend tests, Ruff, TypeScript/Vite build, and Compose config.
- [x] Commit as `feat: serve bounded authenticated dashboard status`.

### Task 5: Auth UI and Settings

**Files:** Create `dashboard/frontend/src/auth.ts`, `dashboard/frontend/src/pages/AuthPage.tsx`, `dashboard/frontend/src/pages/OverviewPage.tsx`, `dashboard/frontend/src/pages/ModelsPage.tsx`, `dashboard/frontend/src/pages/SettingsPage.tsx`, `dashboard/frontend/src/auth.test.tsx`, and `dashboard/frontend/src/pages/SettingsPage.test.tsx`; modify `dashboard/frontend/src/App.tsx`, `dashboard/frontend/src/api.ts`, and `dashboard/frontend/package.json`/lock.

**Interfaces:** Frontend `AuthState` represents `setup-required`, `logged-out`, and `authenticated` states; `AuthClient` uses same-origin fetch with credentials and keeps CSRF/session data only in memory.

- [x] Add Vitest, `@testing-library/react`, and jsdom as frontend-only development dependencies.
- [x] Write frontend tests for setup-required state, token submission without persistence, password submission, login/logout, protected view gating, session revocation, and password change.
- [x] Add tests that enforce five-second visible polling, backoff while the document is hidden, one in-flight status request, and a device-wide label for GPU telemetry.
- [x] Run the frontend tests and verify they fail before implementation.
- [x] Build setup/login/settings views and guard API data until authenticated. Keep the CSRF token and session state in memory; add no nonfunctional controls.
- [x] Run frontend tests, backend tests, Ruff, and the TypeScript/Vite build.
- [x] Commit as `feat: add authenticated dashboard pages`.

### Task 6: Local bootstrap CLI, Windows startup, runtime contract, and docs

**Files:** Create `dashboard/backend/app/bootstrap.py`, `dashboard/scripts/install_dashboard.ps1`, `dashboard/scripts/uninstall_dashboard.ps1`, `dashboard/backend/tests/test_bootstrap_cli.py`, and `dashboard/backend/tests/test_install_scripts.py`; modify `.github/workflows/ci.yml`, `dashboard/frontend/package.json`/lock, `dashboard/README.md`, `docs/DASHBOARD.md`, and `.gitignore` as needed.

**Interfaces:** `python -m app.bootstrap issue-token` creates and prints one random bootstrap token only to the local installer console; `python -m app.bootstrap recover-admin` revokes sessions and issues one recovery token. Installation creates/displays the initial token in the local console. `install_dashboard.ps1` and `uninstall_dashboard.ps1` manage only the named current-user Scheduled Task; they never invoke inference Compose.

- [x] Write tests `test_issue_token_cli_prints_unique_token_only_to_console`, `test_recovery_cli_revokes_sessions_and_preserves_vault_data`, `test_install_script_registers_interactive_logon_restart_task`, `test_install_script_binds_only_loopback_and_does_not_start_inference`, `test_uninstall_script_removes_task_but_preserves_runtime_data`, and `test_runtime_acl_is_user_scoped`.
- [x] Run the tests and verify they fail before implementation.
- [x] Implement issue/recovery commands, restrictive user-scoped data ACLs, and an explicit reversible logon task with restart-on-failure. Do not invoke these install commands during code development.
- [x] Update `engines.node` and all Node requirements to `>=22.12`; keep CI on Node 24 with `npm ci`, `npm run test`, and `npm run build`.
- [x] Document bootstrap, password recovery, sign-in/sign-out lifecycle, local data/vault locations, Docker Desktop privilege boundary, and the still-pending manual acceptance checklist.
- [x] Run backend/frontend tests, Ruff, TypeScript/Vite build, changed YAML lint, Compose config, and `git diff --check`.
- [x] Commit as `feat: add local dashboard startup path`.

**Step A exit check:** From a clean checkout, CI passes; setup issues a unique single-use token; protected APIs require login; status remains truthful with Docker unavailable; controller startup is documented and separately runnable without starting inference; Node docs/metadata/CI agree.

---

## Step B — Safe inference controls and validated profiles

### Task 7: Explicit Compose source-of-truth setting and fixed runner

**Files:** Create `dashboard/backend/app/compose_runner.py`, `dashboard/backend/app/configuration.py`, `dashboard/backend/tests/test_compose_runner.py`, and `dashboard/backend/tests/test_configuration_source.py`; modify `dashboard/config/profiles.yaml` and `docs/DASHBOARD.md`.

**Interfaces:** `LocalSettingsStore.load_compose_root() -> Path | None` reads a protected local setting written only by an installer/CLI; `ComposeRunner(project_root, run, timeout)` exposes `inspect_project()`, `stop_vllm()`, and `start_vllm()` with fixed argv. No API accepts a path or command.

- [x] Write tests `test_controls_unavailable_without_explicit_root`, `test_project_root_must_be_existing_expected_compose_project`, `test_runtime_project_matches_expected_labels_and_network`, `test_profile_values_update_only_allowlisted_env_keys_atomically`, `test_env_update_preserves_comments_and_unrelated_secret_values`, `test_stop_and_start_use_fixed_project_and_service_arguments`, and `test_runner_never_calls_down_or_volume_commands`.
- [x] Verify tests fail before implementation.
- [x] Implement `python -m app.settings configure-compose-root --path <path>` for local-only configuration and validation of Compose file, project identity, external network, cache mount, service, and absent host port. The current active project is in a separate worktree, so do not default this PR branch to that path; show operations unavailable until an operator explicitly configures a stable root.
- [x] Keep only `baseline-4k-2` and `baseline-8k-4` enabled from `dashboard/config/profiles.yaml`; reject modified/unknown IDs and leave any future experimental profile disabled until host validation.
- [x] Implement atomic `.env` updates for only the keys represented by the allowlisted profile, preserve all unrelated content, apply user-only permissions, and validate resulting Compose config before any container action.
- [x] Run focused tests and Ruff.
- [x] Commit as `feat: add explicit inference project configuration`.

### Task 8: Desired state, operation journal, state machine, and recovery

**Files:** Create `dashboard/backend/app/control_state.py`, `dashboard/backend/tests/test_control_state.py`, and `dashboard/backend/tests/test_operations.py`; modify `dashboard/backend/app/persistence.py` and `dashboard/backend/app/status_cache.py`.

**Interfaces:** `ActivityGate.acquire(kind, operation_id, now, lease_seconds) -> bool`, `heartbeat(operation_id, now, lease_seconds) -> None`, and `release(operation_id) -> None` uses a SQLite singleton row and `BEGIN IMMEDIATE`, so it coordinates controller instances as well as in-process calls. Expired leases are reconciled against Docker state before another operation is accepted. `OperationCoordinator(settings, runner, status_reader, contract_checker, clock, gate)` exposes `start(profile_id)`, `stop()`, `restart()`, `apply_profile(profile_id)`, and `reconcile_after_restart()`. Return a durable operation record with ID, phase, timestamps, requested profile/state, outcome category, and sanitized recovery state.

- [x] Write fake-adapter tests `test_single_writer_rejects_conflicting_operation`, `test_two_activity_gate_instances_share_sqlite_lock`, `test_expired_gate_lease_reconciles_before_reuse`, `test_benchmark_gate_rejects_concurrent_inference_mutation`, `test_stop_persists_desired_stopped_before_runner_call`, `test_controller_restart_never_autostarts_stopped_inference`, `test_start_reports_loading_until_health_and_smoke_pass`, `test_candidate_profile_commits_only_after_contract_check`, `test_failure_rolls_back_last_known_good_and_verifies_it`, `test_incomplete_operation_is_reconciled_without_auto_start`, `test_unknown_profile_is_rejected_before_runner`, and `test_operations_never_delete_volumes`.
- [x] Verify tests fail before implementation; use only fake runner/status/checker.
- [x] Implement SQLite-backed single-writer lock and operation journal. Keep a configurable startup deadline with default 10 minutes; health and contract checks are separate; no start is considered successful until both pass.
- [x] On Stop, persist desired stopped before fixed stop call. On restart, reconcile actual Docker state and journal but never auto-start vLLM. On failure, restore last known-good profile and report both outcomes.
- [x] Prove the old service releases `ddu-llm` before any replacement start is issued; retain the single-container invariant throughout switch and rollback.
- [x] Run focused operations tests, backend suite, and Ruff.
- [x] Commit as `feat: add journaled inference operations`.

### Task 9: Authenticated control API, confirmation UI, rollback display

**Files:** Create `dashboard/backend/app/routes/operations.py` and `dashboard/backend/tests/test_operation_routes.py`; create `dashboard/frontend/src/pages/OperationPanel.tsx` and `dashboard/frontend/src/pages/OperationPanel.test.tsx`; modify `dashboard/backend/app/api.py`, `dashboard/frontend/src/App.tsx`, `dashboard/frontend/src/api.ts`, `dashboard/frontend/src/pages/ModelsPage.tsx`, and `docs/DASHBOARD.md`.

- [ ] Write route/UI tests `test_mutation_requires_session_and_csrf`, `test_operation_rejects_unconfigured_compose_root`, `test_profile_picker_sends_only_profile_id`, `test_disruptive_action_requires_explicit_confirmation`, `test_loading_and_rollback_outcomes_are_visible`, and `test_stop_state_survives_frontend_reload`.
- [ ] Verify tests fail before implementation.
- [ ] Add only working Start/Stop/Restart/Apply Profile routes and UI; fixed profile IDs only, explicit confirmation, one active operation, no general command entry.
- [ ] Display long Loading as normal and show candidate/rollback outcomes; preserve a deliberate Stop after refresh/restart.
- [ ] Run backend/frontend suites, Ruff, TypeScript/Vite, and Compose validation.
- [ ] Commit as `feat: expose protected inference controls`.

**Step B exit check:** Fake-driven lifecycle and rollback tests pass; current service API/network contract is unchanged; operations require a stable configured project path; no live mutation has been run.

---

## Step C — Predefined jobs, history, and sanitized logs

### Task 10: Allowlisted job registry and bounded runner

**Files:** Create `dashboard/backend/app/jobs.py`, `dashboard/backend/tests/test_jobs.py`, and `dashboard/backend/tests/fakes.py`; modify `dashboard/backend/app/persistence.py`.

**Interfaces:** `JobRegistry` exposes fixed job IDs `server-readiness`, `api-smoke`, and `performance-benchmark`; `JobRunner(gate, command_adapter, artifact_store).start(job_id, options)`, `cancel(job_id)`, and `status(job_id)` accept only declared bounded options and use a fixed command adapter. The benchmark defaults to concurrency 1 or 2; all numeric options have explicit registry bounds, with higher concurrency limited to an explicit diagnostic choice.

- [ ] Write tests `test_unknown_job_and_arbitrary_arguments_are_rejected`, `test_numeric_bounds_and_concurrency_default_to_one_or_two`, `test_only_one_mutating_or_benchmark_job_runs_at_once`, `test_inference_operation_gate_rejects_concurrent_benchmark`, `test_job_timeout_and_cancel_are_recorded`, `test_job_output_never_contains_prompt_or_completion`, and `test_output_size_and_artifact_retention_are_bounded`.
- [ ] Verify tests fail before implementation.
- [ ] Reuse `scripts/check_server.py`, `scripts/shared_inference_smoke.py`, and `scripts/benchmark.py` through fixed Docker network/mount invocations; do not accept a shell string.
- [ ] Record profile/model revision, engine image digest, host/GPU, script version, concurrency, request count, duration, success/error counts, latency percentiles, and available host/GPU telemetry; default benchmark concurrency to one or two.
- [ ] Store WAL metadata/provenance and sanitized JSONL outside the checkout. Enforce timeout, cancellation, bounded output/retention, and no request content.
- [ ] Run job tests and Ruff.
- [ ] Commit as `feat: add bounded dashboard jobs`.

### Task 11: Job history, compatible comparison, and UI

**Files:** Create `dashboard/backend/app/routes/jobs.py`, `dashboard/backend/tests/test_job_routes.py`, `dashboard/frontend/src/pages/BenchmarksPage.tsx`, and Vitest tests; modify API/frontend navigation, schema migrations, and `docs/DASHBOARD.md`.

- [ ] Write tests `test_job_routes_require_auth_and_csrf`, `test_history_is_bounded_and_sanitized`, `test_only_compatible_runs_compare_as_comparable`, and `test_ui_shows_profile_and_hardware_differences`.
- [ ] Verify tests fail before implementation.
- [ ] Implement protected start/cancel/status/history routes and a simple history/compare view; only display summary fields, no prompt or response contents.
- [ ] Bound retention and make history/artifact cleanup a dry-run preview plus explicit confirmation; compare only runs with compatible profile, engine, hardware, and job/test versions.
- [ ] Run full backend/frontend test/build/lint/Compose checks.
- [ ] Commit as `feat: add dashboard job history`.

### Task 12: Bounded sanitized logs

**Files:** Create `dashboard/backend/app/logs.py`, `dashboard/backend/app/routes/logs.py`, `dashboard/backend/tests/test_logs.py`, `dashboard/frontend/src/pages/LogsPage.tsx`, and Vitest tests; modify `dashboard/backend/app/api.py`, navigation, and docs.

- [ ] Write tests `test_log_tail_limit_is_enforced`, `test_credentials_env_values_and_paths_are_redacted`, `test_prompt_like_content_is_not_stored_or_returned`, `test_docker_unavailable_returns_sanitized_category`, and `test_logs_route_requires_admin_session`.
- [ ] Verify tests fail before implementation.
- [ ] Implement fixed read-only log queries with a small maximum tail, redaction before display/export, no disk copies, and generic failure categories.
- [ ] Run full suites/lint/build/config checks.
- [ ] Commit as `feat: add sanitized dashboard logs`.

**Step C exit check:** Only three fixed job types exist, job state/history/artifacts remain bounded and sanitized, compatible comparisons mark differences, and logs are bounded/authenticated.

---

## Step D — Optional read-only PostgreSQL monitoring

### Task 13: Isolated PostgreSQL probe service

**Files:** Create `dashboard/backend/app/databases.py`, `dashboard/backend/tests/test_databases.py`, and `dashboard/backend/tests/fakes.py`; modify `dashboard/backend/pyproject.toml` and persistence migrations.

**Interfaces:** `DatabaseMonitor(secret_store, connector_factory, connect_timeout, query_timeout, worker_gate)` exposes `configuration_state()`, `check(database_id)`, and `list_status()`. It obtains credentials through `SecretsStore.get_for_service` with the monitoring purpose and enforces a read-only connection/transaction. Missing configuration is `Unconfigured`; failures return short categories without DSN or driver messages. Each check runs in a bounded worker slot with connect/query deadlines and cannot delay controller status.

- [ ] Write tests `test_unconfigured_differs_from_unavailable`, `test_probe_runs_only_select_one`, `test_connect_and_query_deadlines_are_enforced`, `test_connection_failure_does_not_break_status_service`, `test_dsn_never_appears_in_result_or_logs`, and `test_database_credentials_are_read_only_vault_values`.
- [ ] Verify tests fail before implementation.
- [ ] Add the smallest maintained PostgreSQL client dependency; require connection secrets from the vault and never provision accounts/schemas.
- [ ] Force read-only connection/transaction behavior and execute only `SELECT 1`; do not accept DSNs from API callers.
- [ ] Implement bounded isolated probes; failures cannot block status, jobs, or inference.
- [ ] Run database tests and Ruff.
- [ ] Commit as `feat: add optional postgres health monitor`.

### Task 14: Protected database configuration and UI

**Files:** Create `dashboard/backend/app/routes/databases.py`, `dashboard/backend/tests/test_database_routes.py`, `dashboard/frontend/src/pages/DatabasesPage.tsx`, and Vitest tests; modify API/navigation, vault reveal flow, and docs.

- [ ] Write tests `test_database_routes_require_auth_and_csrf`, `test_dsn_is_write_only_and_never_returned`, `test_database_probe_ui_distinguishes_unconfigured_and_down`, and `test_unavailable_database_does_not_hide_other_dashboard_pages`.
- [ ] Verify tests fail before implementation.
- [ ] Add protected add/update/remove probe settings and show state, last check, latency, and short failure category. Allow one-time credential reveal only to the authenticated administrator, never list stored secret values.
- [ ] Run full backend/frontend suites, Ruff, typecheck/build, changed YAML lint, and Compose config.
- [ ] Commit as `feat: add read-only database monitoring`.

## Final verification and acceptance record

### Task 15: Full CI, security review, and operator acceptance docs

**Files:** Modify `.github/workflows/ci.yml`, `README.md`, `dashboard/README.md`, `docs/DASHBOARD.md`; create `docs/DASHBOARD_SECURITY.md` and `docs/DASHBOARD_ACCEPTANCE.md`.

- [ ] Ensure CI runs Python 3.11 tests/lint, frontend Node 24 `npm ci`/tests/typecheck/build, YAML/shell checks, and Compose config without GPU/model downloads.
- [ ] Run from clean dependencies: `python -m pytest -q`, `python -m ruff check scripts tests dashboard\backend\app dashboard\backend\tests`, `npm ci --prefix dashboard\frontend`, `npm --prefix dashboard\frontend test`, `npm --prefix dashboard\frontend run build`, `docker compose --env-file .env.example config --quiet`, yamllint for changed YAML, and `git diff --check`.
- [ ] Review changed code for auth/CSRF/Origin/Host leaks, local path/command validation, secret redaction, default loopback binding, and absence of inference host ports or volume deletion.
- [ ] Complete the manual acceptance record only for checks actually run. Since ordinary implementation must not stop/reconfigure inference, leave stopped/loading/rollback checks pending for a separately authorized operator window. Record exact Windows sign-in/sign-out, Docker Desktop unavailable, DPAPI, NVML, loopback, resource cost, and expected network/model identity evidence.
- [ ] Run one fresh read-only whole-branch review against the source plan; address Critical/Important findings in one test-first fix pass, record deferred Minor findings, and rerun the complete suite. Keep PR #3 open and do not merge or publish.
- [ ] Commit documentation/CI as `docs: record dashboard security and host acceptance`.

---

## Commit and review cadence

- Keep the execution branch `codex/feat-dashboard-readonly-react-control-panel` and update PR #3; do not create a duplicate branch or PR.
- Stage only intended files with explicit paths. The attached plan under `.codex-attachments/` is user-provided, untracked, and must never be included in a commit.
- Each task commit contains its tests and docs. Run tests before committing. Do not force-push; push normal fast-forward updates to the existing PR branch.
- Do not install the Scheduled Task or issue a bootstrap token during code development. Those are explicit operator setup actions after code review; do not stop/restart the live vLLM service.
