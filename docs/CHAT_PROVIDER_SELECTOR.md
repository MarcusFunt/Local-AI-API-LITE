# Chat provider selector status

**Status: cloud provider disabled. M0 preflight recorded: 2026-11-11.** This document records the feasibility gate from the dual-provider implementation plan. The current dashboard remains local-only; it has no ChatGPT sign-in flow, cloud route, selector, or cloud credentials.

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
- The official [Sign in with ChatGPT DevKit](https://github.com/openai/sign-in-with-chatgpt-devkit) contains `packages/local`, but its package manifest marks version `0.1.0` as `private`; `npm view @siwc/local` returned npm `E404`. This means there is no public npm package; the official source is available in the DevKit repository. It has not been pinned or vendored in this checkout.
- The DevKit [Noncommercial License](https://github.com/openai/sign-in-with-chatgpt-devkit/blob/main/LICENSE) limits use, development/testing, and distribution to a noncommercial purpose. Confirm the project's intended use fits before adopting or vendoring the package; commercial use requires separate written permission.
- The SDK requires the host application to supply a `CredentialEncryption` implementation. The official [security guide](https://github.com/openai/sign-in-with-chatgpt-devkit/blob/main/docs/security.md) describes the Electron sample's `safeStorage` provider and says other local runtimes must provide their own OS-backed encryption. The sample app is macOS-only; the proposed Windows host has not validated an encryption adapter or loopback callback.
- The official [cookbook](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt) documents `signIn`, `listModels`, and `streamResponse`. The [inference guide](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference) requires a model discovered for the active account and treats `response.completed` as the success condition.

## M0 work still pending

- The SDK has not been pinned or vendored while the project-use terms are unresolved.
- No host-only proof of concept or mock-SDK smoke test has been written or run.
- Windows user-session and loopback callback behavior have not been tested.
- Identity-only and plan-authorized sessions have not been exercised. The UI must distinguish a signed-in identity from an active `chatgpt.tokens.use.direct` grant before inference is available.
- Token-leak checks have not been performed across process output, files, browser responses, Docker configuration, logs, or errors because no provider bridge exists yet.
- No SIWC sign-in was started, no plan-usage scope was granted or checked, and no synthetic cloud inference was sent. The plan requires the user to initiate live sign-in.

## Gate decision

M0 is not complete, so cloud implementation remains stopped. Before Milestone 1, close the non-live M0 tasks and verify the SDK/license and Windows host behavior. The final M0 gate also requires user-initiated host-local sign-in, a distinguishable plan-usage grant, a synthetic response ending in `response.completed`, and evidence that tokens do not leak. No undocumented ChatGPT web-session route, browser cookie handling, or implicit fallback is used.

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
