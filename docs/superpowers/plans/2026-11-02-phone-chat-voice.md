# Phone Chat + Voice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make the existing Chat + Voice example practical to use in a phone browser over the current Tailscale HTTPS URL.

**Architecture:** Keep the existing React dashboard and authenticated same-origin API. Add touch-focused recording feedback and phone-specific CSS for the shared navigation and Chat + Voice page; no new runtime dependency or hosting change.

**Tech Stack:** React 19, TypeScript, plain CSS, Vitest, Testing Library, Vite.

**Spec:** `docs/superpowers/specs/2026-11-02-phone-chat-voice-design.md`

## Global Constraints

- Fit 320–430 CSS-pixel phone widths without horizontal scrolling.
- On narrow screens, use composer text at least 16px and interactive targets at least 48px tall.
- Preserve authenticated dashboard, Tailscale-only hosting, chat behavior, and review-before-send transcription.
- Do not add a PWA, offline mode, or new runtime dependency.

## Review Focus

- **iPhone focus zoom:** the message field must remain at least 16px at phone widths; verify computed size at 320px, 390px, and 430px.
- **Touch cancellation or scroll gesture:** pointer cancellation must stop tracks and discard audio; exercise `pointercancel` and window-level `pointerup` in component tests.
- **Denied or unavailable microphone:** display permission or HTTPS guidance without sending audio; test insecure-context and denied-permission paths.
- **Keyboard and safe-area reachability:** composer and voice actions must remain reachable with narrow viewport and safe-area padding; inspect full-page screenshots at phone widths.
- **Spoken reply playback:** keep native audio controls visible and full-width on phones; verify the audio element remains present after synthesis.

---

### Task 1: Make phone recording states actionable

**Files:**
- Modify: `dashboard/frontend/src/pages/ExamplesPage.tsx`
- Test: `dashboard/frontend/src/pages/ExamplesPage.test.tsx`

**Interfaces:**
- Consumes: existing `navigator.mediaDevices.getUserMedia`, `MediaRecorder`, pointer handlers, and Tailscale HTTPS page origin.
- Produces: explicit HTTPS guidance when the page is not a secure context, plus a clear permission-denied message that names the browser microphone setting.

- [x] **Step 1: Add failing tests**
  - Test that an insecure context reports HTTPS/Tailscale guidance before calling `getUserMedia`.
  - Test that a denied microphone request reports actionable permission guidance and never transcribes.
  - Keep the existing pointer-cancel test asserting tracks stop and audio is discarded.
- [x] **Step 2: Run the focused tests and confirm the new assertions fail for the expected missing messages**

  Run from `dashboard/frontend`: `npm test -- --run src/pages/ExamplesPage.test.tsx`

- [x] **Step 3: Implement the minimum secure-context and permission feedback**
  - Check `window.isSecureContext` before microphone access.
  - If false, show that microphone capture requires HTTPS and direct the user to the existing Tailscale HTTPS URL.
  - Preserve current behavior for supported secure browsers and the existing 120-second recording limit.
- [x] **Step 4: Run the focused tests and confirm they pass**

### Task 2: Optimize the shared shell and Chat + Voice page for phones

**Files:**
- Modify: `dashboard/frontend/src/styles.css`
- Verify: `dashboard/frontend/src/pages/ExamplesPage.tsx`
- Test: `dashboard/frontend/src/pages/ExamplesPage.test.tsx` plus browser viewport inspection

**Interfaces:**
- Consumes: existing `.sidebar`, `.side-nav`, `.main-area`, `.example-card`, `.chat-panel`, `.example-composer`, `.voice-controls`, and `.spoken-reply` classes.
- Produces: a compact horizontally scrollable phone navigation; readable 16px composer text; 48px or larger send, clear, and recording targets; touch-safe press-and-hold; safe-area-aware spacing; and responsive spoken-reply controls.

- [x] **Step 1: Add a failing small-screen layout assertion if the current test harness can verify it without mocking layout**
  - If CSS layout is not measurable in JSDOM, use browser viewport inspection as the test and do not add a brittle CSS-string assertion.
- [x] **Step 2: Implement the phone rules in the existing `max-width: 760px` media query**
  - Keep navigation in one horizontally scrollable row and hide desktop-only ordinal labels.
  - Increase phone control sizes and composer type; set `touch-action: none` and disable text selection on the hold-to-talk button.
  - Add `env(safe-area-inset-*)` padding where it protects bottom controls; use dynamic viewport units where appropriate.
  - Keep native audio controls and status text visible without forcing a desktop-width panel.
- [x] **Step 3: Verify 320px, 390px, and 430px phone viewports**
  - Confirm `document.documentElement.scrollWidth <= window.innerWidth`.
  - Confirm composer computed font size is at least 16px and main touch controls are at least 48px high.
  - Inspect that chat, composer, send, hold-to-talk, microphone feedback, and audio playback can be reached by scrolling.
- [x] **Step 4: Run all frontend tests and the production build**

  Run from `dashboard/frontend`: `npm test` and `npm run build`.

### Task 3: Deploy the built frontend and verify the phone route

**Files:**
- Generated: `dashboard/frontend/dist/` (existing deployment artifact; do not commit generated output unless repository conventions require it)

- [x] **Step 1: Build the frontend after all tests pass**
- [x] **Step 2: Confirm the running dashboard serves the updated page from the existing Tailscale HTTPS route**
- [x] **Step 3: Confirm the route is still tailnet-only and the app still binds only to loopback**
- [x] **Step 4: Leave the current conversation, authentication, and speech containers running**
