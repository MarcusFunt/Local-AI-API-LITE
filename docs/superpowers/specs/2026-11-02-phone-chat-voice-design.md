# Phone Chat + Voice Design

## Intent

Make the existing Chat + Voice example comfortable to use from a phone browser over the current Tailscale HTTPS URL. A phone user should be able to read replies, type and send a prompt, hold to record, review the transcript, and play spoken replies without desktop-sized controls or browser zoom disrupting the flow.

## Goals

- Fit common 320–430 CSS-pixel phone widths without horizontal scrolling.
- Give the Chat + Voice page a compact mobile header/navigation and keep its status, conversation, composer, and voice actions easy to reach.
- Use phone-sized controls and 16px-or-larger composer text so mobile Safari does not zoom the page when the field is focused.
- Make press, hold, release, cancellation, recording, and microphone-permission states clear and touch-safe.
- Preserve the current authenticated dashboard, Tailscale-only hosting, chat behavior, and review-before-send transcription flow.

## Non-goals

- Add a PWA, offline mode, push notifications, or a separate phone application.
- Change Tailscale Serve, administrator authentication, speech model deployment, or the chat/voice API contracts.
- Redesign the desktop dashboard or unrelated dashboard pages.

## Approaches

1. **Phone browser over the existing Tailscale URL (selected by Codex from the current session context).** Improve the existing responsive layout and touch recording experience. This reuses the current HTTPS route and keeps login and service behavior unchanged.
2. **Installable PWA.** Adds home-screen installation and app-shell behavior, but needs manifest/icon/install handling and does not remove phone-browser microphone constraints. It is outside the requested phone-use scope.

## Design calls

- Keep the dashboard navigation available but make its phone presentation compact and avoid spending several rows of the short screen on it.
- Prioritize the active example: compact dependency/status details, readable conversation messages, a composer with a clear send action, and an unmistakable hold-to-talk control.
- On narrow screens, make the composer readable at 16px or more and interactive targets at least 48px tall; respect device safe areas and the changing mobile viewport.
- Prevent touch scrolling/selection from interrupting a hold gesture. Continue to stop on release, pointer cancellation, blur, and the existing 120-second limit.
- Explain that microphone capture requires HTTPS and an allowed browser microphone permission when access is unavailable.

## Verification

- Run the frontend test suite and production build.
- Inspect the page at representative phone widths (320px, 390px, and 430px) for overflow, reachable controls, and legible text.
- Exercise touch-style recording start/release/cancel, microphone denial, transcript review/edit/send, and spoken reply playback with browser test doubles; actual device microphone permission remains a phone-side check.
