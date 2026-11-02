# Chat + Voice Dedicated Page Design

## Intent

Give Chat + Voice a focused, stable URL that is easy to revisit and demonstrate. The catalog lives at `/examples`; starting from it takes the signed-in user to `/examples/chat-voice` after the consumer reaches the ready state.

## Goals and constraints

- Keep the dedicated page behind the existing dashboard sign-in.
- Make the route work on direct navigation, browser back/forward, and reload.
- Keep lifecycle ownership in the existing dashboard API and `ExampleRunner`.
- Show the current example status and its existing Start/Stop controls on the page. If the consumer is stopped, the page remains useful and offers Start.
- Provide a clear return path to the Examples catalog.
- Keep chat history and drafts in browser memory. Reloading the page clears the conversation, matching current behavior.
- Do not add per-run URLs, server-side conversation storage, or a public unauthenticated route.

## Approaches

1. **Stable authenticated path (recommended and selected):** `/examples/chat-voice` renders a dedicated workspace and supports reloads/deep links. Starting navigates there when ready. This fits the single globally managed example and avoids introducing persisted user conversations.
2. **Per-run URLs:** each start creates a separate demo session and stores its messages. This supports replaying a particular demonstration, but adds conversation storage, cleanup, and session ownership rules.
3. **URL fragment route:** store the page in `/#/examples/chat-voice`. This avoids server fallback changes, but gives the example a less natural URL and fragment-based navigation.

## Implementation calls

- Add lightweight browser-history routing to the existing React application; no routing dependency is needed for the single dedicated path.
- Render the focused example workspace without the dashboard operations sidebar, with a return link to the catalog and the current status/lifecycle controls.
- Preserve the current route through authentication so a direct link can be opened after sign-in.
- Extend the production static-file fallback to serve the SPA entry point for the known example route, while leaving API route handling unchanged.
- Starting from the catalog navigates only after the existing lifecycle status reaches `ready`. A failed start stays in the catalog with its existing error. Opening the workspace directly reads status and shows either the ready workspace or its startable state.

## Validation

Verify start-to-route navigation, direct route loading, reload and browser history, authenticated access, stopped/starting/ready states, and that chat/voice actions still use the existing authenticated APIs. Run the frontend tests, typecheck, and production build, plus relevant backend tests for SPA route serving.
