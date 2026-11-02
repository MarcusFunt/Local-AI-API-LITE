# Chat + Voice GUI Refresh Design

## Intent

Make the authenticated Chat + Voice workspace feel dynamic, modern, and intuitive while keeping conversation the main task and preserving comfortable phone use.

## Current state

The workspace already has a dedicated authenticated URL and responsive touch controls. It currently stacks lifecycle status, service dependencies, language and response-depth settings, the transcript, message composer, and voice controls as separate panels. This makes the page functional but gives the conversation and its next action less visual priority than the surrounding controls.

## Goals

- Make the conversation the visual center of the workspace, with a clear empty state and distinct user, assistant, pending, and error messages.
- Keep the message composer and push-to-talk action easy to find on desktop and phones.
- Make current example and speech readiness visible at a glance, with detailed dependencies and recovery guidance available without dominating the page.
- Give the selected language clear placement because it controls chat, STT, and TTS together.
- Provide restrained feedback for sending, recording, transcription, synthesis, and lifecycle changes without implying token streaming or audio levels the app does not expose.
- Preserve keyboard access, screen-reader announcements, adequate contrast, touch targets, and reduced-motion preferences.

## Non-goals

- Change authentication, the `/examples/chat-voice` route, lifecycle ownership, or inference and speech API contracts.
- Add token streaming, persisted conversations, new inference features, or new runtime dependencies.
- Redesign unrelated dashboard pages or turn this into a voice-only interface.

## Approaches

1. **Conversation-first workbench (selected):** Give the transcript and composer the main visual weight; keep lifecycle status compact, settings grouped, and service details expandable. This matches the chat-and-voice workflow and retains the existing phone-friendly single column.
2. **Split-pane operator view:** Keep service and lifecycle details continuously visible beside chat. This helps desktop troubleshooting but competes with conversation space and is cramped on phones.
3. **Voice-focused canvas:** Center the microphone and animated recording treatment. This can make voice feel vivid but makes text chat secondary and increases accessibility and interaction complexity.

## Design calls

- Build on the existing dark green and mint palette; improve spacing, typography, contrast, and surface hierarchy without a new brand or dependency.
- Use a compact workspace header with the example name, an honest lifecycle state, and clear return/navigation actions.
- Present service readiness as a concise summary with expandable dependency details and actionable error text.
- Make the transcript a calm, readable conversation surface. The empty state should offer optional starter prompts that fill the composer without sending automatically.
- Keep Danish/English selection visible and group response-depth controls as a compact three-choice setting: Fast, Balanced, and Quality.
- Keep the composer available near the bottom of the workspace, with a clear send action and a distinct hold-to-talk control. On phones, respect safe areas and the virtual keyboard, retain at least 48px interactive targets, and prevent horizontal overflow at 320px width.
- Show recording with a timer and a restrained microphone pulse; do not draw a simulated waveform or claim that a reply is streaming. Announce asynchronous state changes accessibly.
- Keep motion short and purposeful, disable or reduce nonessential transitions when `prefers-reduced-motion: reduce` is active, and do not make animation the only state cue.

## Success criteria

- A first-time user can identify readiness, start a message, choose a language, and find voice input without reading a long instruction block.
- The conversation remains readable while composing and when replies are long; the active composer and voice action stay reachable on phone screens.
- The layout works at 320px, 390px, 430px, and desktop widths without horizontal scrolling or obscured controls.
- Loading, ready, starting, stopping, failed, recording, transcribing, and synthesis states remain truthful, distinguishable, and accessible.
- Existing authentication, lifecycle behavior, Danish/English inference routing, in-memory conversation behavior, and speech flows remain unchanged.

## Project constraints

- Use the existing React 19, TypeScript, Vite, and CSS stack.
- Do not add frontend dependencies.
- Preserve the current dashboard sign-in requirement and stable workspace route.
- Keep conversation data in browser memory and use the existing chat, STT, and TTS APIs.
