# Cutover and rollback

This runbook applies only after a candidate image and model have passed their standalone contract and RTX 3060 checks. The history backend owns its database, application state, and client adapter; this repository owns the model server.

## Before cutover

1. Confirm the history database has a current backup and record its container and volume names. Never reset or remove it for an inference change.
2. Record the original llama.cpp compose command, GGUF path, image digest, and environment file outside Git.
3. Check all running containers and `ddu-llm` alias ownership. Do not stop AI-Stack services. If the existing inference container is absent, skip the old-service stop step.
4. Verify the new Local-AI-API-LITE deployment and private network with a throwaway trusted client.

## Cutover sequence

1. Apply and CI-test the history client compatibility changes while preserving M2 code and database state.
2. Stop only the old inference Compose project. Do not stop the database/API/worker project and do not run `down -v`.
3. Confirm the `ddu-llm` alias is unclaimed.
4. Start this vLLM Compose project and verify `/health`, `/v1/models`, and the structured Danish probe from the history worker.
5. Check history `/healthz`, `/readyz`, and `/api/v1/status`; run the M2 fake-turn/replay smoke test.
6. Stop and restart inference once. Confirm the worker reports degraded/recovered status and the fake M2 turn still completes without inference.
7. Archive the old llama.cpp configuration as a rollback reference only after the checks pass. Keep it out of the active inference deployment.

## Rollback triggers

Rollback if the model cannot load on the RTX 3060, the structured response contract breaks, readiness does not recover, output is truncated or malformed, alias/port conflicts appear, OOM recurs, or M2 fake-turn integrity checks fail.

## Rollback sequence

1. Stop the vLLM project.
2. Confirm no container claims `ddu-llm`.
3. Start the previously recorded llama.cpp Compose project using its original pinned model/image and environment.
4. Rerun its previous smoke check and the history client probe.
5. Leave the database/backend project and its volume untouched. Record the failure and revise the vLLM candidate before trying again.

## Rollback drill (2026-10-28)

The rollback was exercised on the RTX 3060 host against the stopped, isolated `ddu-migration-smoke` backend and its existing PostgreSQL volume. The original llama.cpp image `ghcr.io/ggml-org/llama.cpp@sha256:d87b02aafa74797a2d599b281c50468704bd0bcdc57bfcf31b570d593f1d285b` and `Qwen3.5-4B-Q4_K_M.gguf` were used; the GGUF SHA-256 matched the archived M1.5 record (`00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4`). The historical Compose file is recoverable from the companion repository's Git history at commit `5dc7d56` (`deployment/inference/compose.yaml`); it is not part of the active deployment.

The vLLM service was stopped, the old service claimed `ddu-llm` with no host port mapping, and the backend was started using its existing volume `ddu-migration-smoke_pgdata`. `/healthz` and database-backed `/readyz` returned healthy, `/api/v1/status` reported inference ready, and the app-owned Danish schema probe passed. The M2 fake-turn/replay smoke passed against that same database volume. Stopping llama.cpp left the backend ready and inference degraded; the fake-turn/replay smoke still passed. vLLM was restarted on the same alias, the backend reported recovery without a restart, and the Danish probe plus fake-turn/replay smoke passed again. The PostgreSQL volume name was unchanged throughout; no reset or volume removal was run.

The vLLM service was left running healthy. The isolated backend project was stopped while retaining its test volume.
