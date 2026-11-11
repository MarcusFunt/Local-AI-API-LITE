# Host-side Sign in with ChatGPT preflight

This Windows-only CLI is an isolated M0 proof of feasibility. It is not connected to the dashboard, Docker, or the local Qwen service. No live OAuth has been started, and the ChatGPT provider remains disabled in the product.

## Requirements and setup

- Windows with Node.js 22 or later and npm.
- Initialize the pinned official DevKit submodule from the repository root: `git submodule update --init integrations/siwc-host/vendor/devkit`.
- In this directory, run `npm ci`, then `npm run build` and `npm test`.

The host package lockfile is separate from the DevKit's repository lockfile. `npm audit --audit-level=high` checks this host package's installed tree.

## Commands

Run these from this directory:

```powershell
npm start -- status
npm start -- connect
npm start -- models
npm start -- smoke
```

`status` reads the selected local profile and prints only safe status fields. It does not open a browser. `connect` requires a terminal confirmation before opening the system browser, then asks the official SDK to request identity and plan-sharing permission with the persisted host ID. ChatGPT's authorization page is the separate consent step. `models` requires the actual direct token-use grant and confirmation before querying and printing the account-visible catalog; it sends no prompt. `smoke` requires the same grant and asks again before sending one synthetic prompt to a dynamically discovered model. A signed-in identity without the grant cannot list models or run the smoke request.

Do not run `connect` until you intend to start the real sign-in. No test or build command starts OAuth, opens a browser, or sends a cloud inference request.

## Credential storage

The SDK stores its local state under `%LOCALAPPDATA%\Local-AI-API-LITE\siwc`. The host supplies current-Windows-user DPAPI encryption for the credential payload; there is no plaintext fallback. The SDK keeps its stable `urn:uuid:` host identifier in the separate non-secret `chatgpt-host.json` file and includes it as `ext_agent_host_id` during authorization. The app name and filesystem ID stay stable across launches. The callback listener is the SDK's `127.0.0.1` loopback listener with a dynamically selected port.

Do not copy this data directory into the repository or share it. If DPAPI is unavailable or decryption fails, the host returns a generic error and preserves existing credential data.

## License and scope

The official DevKit is pinned as a git submodule at `0a36fefeb913055c8c7a1a29b63d82b96b2e841a` and is distributed under its Noncommercial License. This preflight work does not establish permission for commercial use. Written permission is required before using the DevKit for a commercial purpose. The app has not been integrated into the dashboard and is not a released cloud feature.

## M0 status

Mocked SDK command tests cover identity-only versus plan-authorized state, explicit consent, dynamic model discovery, synthetic inference, output redaction, and error redaction. Windows DPAPI tests use only synthetic data. The official SDK tests exercise the loopback callback with mocked OAuth responses. These checks do not establish account eligibility, the real ChatGPT authorization result, actual plan-use grant, live inference, browser/profile behavior, or production token-leak guarantees. The M0 gate remains open until the user initiates sign-in, confirms the grant, and explicitly authorizes the synthetic smoke request.
