# Chat provider selector status

**Status: cloud provider disabled. Host-only M0 POC ready for review; M0 gate not passed (2026-11-11).** This document records the feasibility gate from the dual-provider implementation plan. The current dashboard remains local-only; it has no ChatGPT sign-in flow, cloud route, selector, or cloud credentials.

## Existing local path

The Examples workspace sends typed chat to the authenticated, CSRF-protected dashboard route `POST /api/control/v1/examples/chat-voice/chat`. The dashboard `ExampleProxy` forwards it to the existing Chat + Voice container, which calls the local vLLM `ddu-narrator` alias. Speech recognition and synthesis use separate routes. The local provider is the only implemented provider.

## Existing interfaces and lifecycle

- Chat is a JSON POST with `language` (`da` or `en`), `preset` (`fast`, `balanced`, or `quality`), and `messages` (1-16 user/assistant messages with non-empty content). The final message must be from the user; content is limited to 16,000 characters per message and 64,000 across the conversation. The JSON reply contains `text`, with optional bounded `elapsed_ms` and `usage.prompt_tokens` / `usage.completion_tokens`.
- Transcription is a `multipart/form-data` POST with `file` and `language` (`da` or `en`). Accepted audio types are WebM, WAV, Ogg, MPEG, and MP4; the dashboard limit is 25 MB. The JSON reply contains `text`.
- Synthesis is a JSON POST with `text` and `language` (`da` or `en`); it returns WAV audio.
- Example status requires an administrator session. POST operations require the session CSRF token. Chat readiness comes from the local `ddu-narrator` dependency; voice readiness separately depends on `ddu-stt` and `ddu-tts`. Start/stop share the dashboard activity reservation. [EXAMPLES.md](EXAMPLES.md) documents readiness, startup, idle timeout, and recovery behavior.

## M0 findings

- The inspected checkout and fetched `origin/main` both pointed to `ae04ec39163700eaa1952a63cc07ab42f1054db9`.
- Host Node.js is 24.15.0, above the official local package's declared Node.js `>=22` engine.
- The official [Sign in with ChatGPT DevKit](https://github.com/openai/sign-in-with-chatgpt-devkit) contains `packages/local`, but its package manifest marks version `0.1.0` as `private`; `npm view @siwc/local` returned npm `E404`. The official source is pinned as a git submodule at `0a36fefeb913055c8c7a1a29b63d82b96b2e841a`; the host uses a separate npm workspace and lockfile.
- The DevKit [Noncommercial License](https://github.com/openai/sign-in-with-chatgpt-devkit/blob/main/LICENSE) limits use, development/testing, and distribution to a noncommercial purpose. This preflight does not establish permission for commercial use. Written permission is required before commercial adoption.
- The SDK requires the host application to supply a `CredentialEncryption` implementation. The official [security guide](https://github.com/openai/sign-in-with-chatgpt-devkit/blob/main/docs/security.md) describes the Electron sample's `safeStorage` provider and says other local runtimes must provide their own OS-backed encryption. The host POC supplies current-user Windows DPAPI and passed synthetic round-trip/tamper tests.
- The official [cookbook](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt) documents `signIn`, `listModels`, and `streamResponse`. The [inference guide](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference) requires a model discovered for the active account and treats `response.completed` as the success condition.

## Host-only M0 proof of concept

- A host-only CLI lives in [`integrations/siwc-host/README.md`](../integrations/siwc-host/README.md). It supports `status`, explicitly confirmed `connect`, separately confirmed `models`, and a separately confirmed `smoke`. It is not connected to the dashboard, Docker, or local Qwen path.
- The host uses the SDK's stable persisted `urn:uuid:` host ID, includes it as `ext_agent_host_id`, and uses the system-browser OAuth flow with a `127.0.0.1` callback, state, nonce, and PKCE. The host stores SDK data in `%LOCALAPPDATA%\Local-AI-API-LITE\siwc`.
- Mock-SDK tests distinguish disconnected, identity-only, and plan-authorized states; require confirmation before connect, model catalog lookup, or smoke; prevent model lookup and smoke before the direct token-use grant; discover models dynamically; and redact token-like fields and error messages.
- On Windows, the DPAPI adapter passed synthetic encrypt/decrypt and tamper-rejection checks. The SDK's package suite passed **56 tests with 1 skipped**, including mocked loopback tests that exercise the host-local callback, state validation, PKCE, and OAuth token exchange without opening a browser or contacting an account.
- The host npm audit reports zero vulnerabilities. The pinned upstream DevKit repository lockfile separately lists `undici@7.29.1`; the host workspace lockfile does not install `undici`. Recheck this finding before adopting or updating the submodule.
- The current process and 28 detected Edge/Chrome/Firefox processes run under the same Windows user and session. The planned `%LOCALAPPDATA%\Local-AI-API-LITE\siwc` directory does not exist yet; the parent Local AppData ACL is owned by the current user. No authenticated profile files exist to inspect, and a real system-browser/profile callback remains unverified.

## M0 work still pending

- Project use has not been confirmed as compatible with the DevKit's Noncommercial License; commercial use needs written permission.
- No real SIWC sign-in was started, no actual account identity or `chatgpt.tokens.use.direct` grant was checked, and no real model catalog was returned.
- No synthetic cloud inference was sent, so there is no real `response.completed` evidence. The plan requires the user to initiate sign-in and separately consent to the synthetic prompt.
- Token-leak checks cover mock CLI output/errors and SDK authorization URL tests only. Live process output, credential files, browser traffic, Docker configuration, logs, and dashboard responses still require review after an authorized connection exists.
- The real default-browser process/session and Windows storage ACLs for an authenticated profile have not been validated. The POC CLI is not a supervised IPC service, and no dashboard bridge exists.

## Gate decision

M0 is not complete, so Milestones 1-6 remain stopped. The host-only POC and non-live tests are available for review. Close the license/use question and remaining Windows checks, then the user can initiate host-local sign-in. The gate also requires a distinguishable actual plan-usage grant, a synthetic response ending in `response.completed`, and complete token-leak evidence. No undocumented ChatGPT web-session route, browser cookie handling, or implicit fallback is used.

Until that gate passes:

- Chat + Voice continues to use the current local Qwen path and its existing readiness checks.
- The dashboard has no ChatGPT provider control or route, and no feature flag claims the cloud feature is available.
- Local STT and TTS remain separate from text provider selection.
- A future cloud path must keep tokens on the trusted host, use the public Responses API, discover account-visible models, and require explicit cloud selection and prompt/history disclosure.

## Baseline checks

Commands and outcomes on the isolated `codex/dual-provider-selector` worktree:

- `python -m pytest -q` after installing `requirements-dev.txt`: **499 passed, 3 skipped**; one existing Starlette/httpx deprecation warning.
- `ruff check scripts tests dashboard/backend`: **passed**.
- Frontend `npm test`: **13 files, 131 tests passed**.
- Frontend `npm run build`: **passed**.
- `docker compose config --quiet`: **passed** when the required model/cache values were supplied from `.env.example`; without `.env` or those values Compose reports the expected missing-variable errors.

The source checkout's two untracked audio files were left untouched.
