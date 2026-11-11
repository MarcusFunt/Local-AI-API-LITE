# Chat provider selector status

**Status: cloud provider disabled. M0 assessment: 2026-11-11.** This document records the feasibility gate from the dual-provider implementation plan. The current dashboard remains local-only; it has no ChatGPT sign-in flow, cloud route, selector, or cloud credentials.

## Existing local path

The Examples workspace sends typed chat to the authenticated, CSRF-protected dashboard route `POST /api/control/v1/examples/chat-voice/chat`. The dashboard `ExampleProxy` forwards it to the existing Chat + Voice container, which calls the local vLLM `ddu-narrator` alias. Speech recognition and synthesis use separate routes. The local provider is the only implemented provider.

## M0 findings

- The inspected checkout and fetched `origin/main` both pointed to `ae04ec39163700eaa1952a63cc07ab42f1054db9`.
- Host Node.js is 24.15.0, above the official local package's declared Node.js `>=22` engine.
- The official [Sign in with ChatGPT DevKit](https://github.com/openai/sign-in-with-chatgpt-devkit) contains `packages/local`, but that package is version `0.1.0` and marked `private`; `npm view @siwc/local` returned npm `E404`. It is a workspace in the DevKit repository rather than an installable npm dependency. Pinning or vendoring it would require a deliberate source and license decision.
- The DevKit is under OpenAI's [Noncommercial License](https://github.com/openai/sign-in-with-chatgpt-devkit/blob/main/LICENSE). Confirm that this project's intended use fits that license before redistributing the SDK or a modified copy.
- The SDK requires the host application to supply a `CredentialEncryption` implementation. The official [security guide](https://github.com/openai/sign-in-with-chatgpt-devkit/blob/main/docs/security.md) describes the Electron sample's `safeStorage` provider and says other local runtimes must provide their own OS-backed encryption. The sample app is macOS-only; the proposed Windows host has not validated an encryption adapter or loopback callback.
- The official [cookbook](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt) documents `signIn`, `listModels`, and `streamResponse`. The [inference guide](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference) requires a model discovered for the active account and treats `response.completed` as the success condition.
- No SIWC sign-in was started, no plan-usage scope was granted or checked, and no synthetic cloud inference was sent. The Windows host proof and M0 gate therefore remain incomplete.

## Gate decision

Per M0, cloud implementation stays stopped until the host-local OAuth path, protected credential storage, actual granted plan-usage permission, and one synthetic completed response are verified with the user's explicit authorization. No undocumented ChatGPT web-session route, browser cookie handling, or implicit fallback is used.

Until then:

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
