# Chat + Voice GUI Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh the authenticated Chat + Voice workspace into a dynamic, modern, conversation-first workbench that remains clear and comfortable on phones.

**Architecture:** Keep the existing React page, lifecycle API, chat/STT/TTS contracts, and in-memory conversation model. Reorganize the workspace into a compact status header, readable conversation surface, grouped settings, and a reachable composer/voice dock; use CSS for responsive layout and restrained motion.

**Tech Stack:** React 19, TypeScript, CSS, Vitest, Testing Library, Vite.

**Spec:** `docs/superpowers/specs/2026-11-02-chat-voice-gui-refresh-design.md`

## Global Constraints

- Use the existing React 19, TypeScript, Vite, and CSS stack.
- Do not add frontend dependencies.
- Preserve the current dashboard sign-in requirement and stable workspace route.
- Keep conversation data in browser memory and use the existing chat, STT, and TTS APIs.
- Do not change authentication, lifecycle ownership, inference or speech API contracts, or unrelated dashboard pages.
- Keep status labels truthful; do not imply token streaming or audio levels the app does not expose.
- Honor `prefers-reduced-motion: reduce` and keep every action keyboard and screen-reader accessible.

## Review Focus

- **Lifecycle status transition:** Starting, stopping, failed, and recovery-required states must remain distinguishable and retain the correct available actions. Test in Task 1 with a state matrix.
- **Speech service unavailable:** Chat stays usable while the interface explains why voice actions are unavailable. Test in Task 1 with voice dependency stopped and unavailable states.
- **Empty or long conversation:** Starter prompts fill the composer without sending; long reply text and its speech action remain available. Test behavior in Task 2 and visual wrapping in Task 4.
- **Language and response preset:** Selecting Danish/English and Fast/Balanced/Quality must preserve the existing API values for chat, transcription, and synthesis. Test in Task 3.
- **Phone viewport, keyboard, and reduced motion:** At 320px width the composer, send action, and voice action stay reachable without horizontal overflow; nonessential animation is reduced. Validate in Task 4 with responsive browser inspection and CSS reduced-motion checks.

---

## File Structure

- Modify `dashboard/frontend/src/pages/ExamplesPage.tsx` for semantic workspace sections, the compact status disclosure, starter prompts, grouped controls, and recording feedback. Keep lifecycle, chat, and speech handlers connected to their current APIs.
- Modify `dashboard/frontend/src/styles.css` for the conversation-first visual hierarchy, responsive composer dock, clear state feedback, and reduced-motion rules. Keep selectors scoped to the example workspace where possible.
- Modify `dashboard/frontend/src/pages/ExamplesPage.test.tsx` for behavior and accessibility regressions using existing Vitest and Testing Library utilities.
- Do not modify `App.tsx`, backend routes, API types, or package dependencies for this visual refresh.

### Task 1: Establish a clear workspace header and service status

**Files:**
- Modify: `dashboard/frontend/src/pages/ExamplesPage.tsx`
- Modify: `dashboard/frontend/src/styles.css`
- Test: `dashboard/frontend/src/pages/ExamplesPage.test.tsx`

**Interfaces:**
- Consumes: `ExamplePublicStatus`, `workspace`, and the existing `start`, `stop`, and `refresh` handlers.
- Produces: A workspace header that identifies Chat + Voice, exposes the current lifecycle state, and keeps Start/Stop available; service dependency details are disclosed through an accessible summary control. The catalog layout remains intact.

- [ ] **Step 1: Add workspace status tests**

In `ExamplesPage.test.tsx`, render workspace states `ready`, `starting`, `stopping`, `failed`, and `recovery-required`; assert the named workspace status region exposes the matching status and Start/Stop action states. Add a case with voice dependencies stopped that keeps the text composer available and explains the voice limitation. Add a disclosure test proving service details are collapsed initially and available when expanded.

- [ ] **Step 2: Run the focused tests and confirm the new assertions fail**

Run from `dashboard/frontend`: `npm test -- src/pages/ExamplesPage.test.tsx`

Expected: the named workspace status region and service-details disclosure assertions fail because the current workspace has neither structure.

- [ ] **Step 3: Implement the compact workspace header**

In `ExamplesPage.tsx`, render the workspace status/actions in a workspace-specific `<section aria-label="Chat + Voice workspace">` and group the page title, `StateBadge`, and existing lifecycle controls there. Keep dependency rows and recovery guidance inside a native `<details>` disclosure with a visible summary such as “Services and status details.” Do not change the catalog presentation or lifecycle handlers.

- [ ] **Step 4: Style the header and disclosure**

In `styles.css`, use spacing and surface contrast to distinguish the title/status/action row from expanded diagnostic details. Preserve clear focus-visible treatment and touch-sized buttons on mobile.

- [ ] **Step 5: Run the focused frontend test**

Run: `npm test -- src/pages/ExamplesPage.test.tsx`

Expected: the status matrix, voice-unavailable behavior, and disclosure tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add dashboard/frontend/src/pages/ExamplesPage.tsx dashboard/frontend/src/styles.css dashboard/frontend/src/pages/ExamplesPage.test.tsx
git commit -m "feat(chat-voice): clarify workspace status"
```

### Task 2: Make the conversation surface easier to start and read

**Files:**
- Modify: `dashboard/frontend/src/pages/ExamplesPage.tsx`
- Modify: `dashboard/frontend/src/styles.css`
- Test: `dashboard/frontend/src/pages/ExamplesPage.test.tsx`

**Interfaces:**
- Consumes: The existing `messages`, `draft`, `sending`, `chatElapsedMs`, `speak`, and `submitMessage` behavior.
- Produces: A clear empty state with prompt suggestions that populate—but never submit—the composer; visually distinct user, assistant, pending, and error states; readable long messages with their existing speech actions.

- [ ] **Step 1: Add conversation onboarding and message tests**

Test that each starter prompt fills the message field without calling `sendChatTurn`; test that user and assistant articles have accessible names “Your message” and “Assistant reply”; test that a long reply remains intact and keeps the “Speak reply” action available; test that waiting and timeout states remain announced through a status region.

- [ ] **Step 2: Run the focused tests and confirm the new assertions fail**

Run: `npm test -- src/pages/ExamplesPage.test.tsx`

Expected: starter prompt controls and the proposed message-article accessible names are absent.

- [ ] **Step 3: Implement the empty state and conversation semantics**

In `ExamplesPage.tsx`, add three short starter prompts relevant to local chat. Their click handlers only set `draft`. Keep the current `sendChatTurn` flow as the sole send path. Give user and assistant message articles the accessible names “Your message” and “Assistant reply”; keep pending/error content in clear accessible status regions without changing API behavior.

- [ ] **Step 4: Style the transcript and message hierarchy**

In `styles.css`, set a readable maximum conversation width, distinct but restrained user/assistant surfaces, comfortable line height, and overflow wrapping. Keep the response-time and speak-reply actions attached to the assistant message they describe.

- [ ] **Step 5: Run the focused frontend test**

Run: `npm test -- src/pages/ExamplesPage.test.tsx`

Expected: prompt selection, no-auto-send behavior, message names, long-reply content/actions, and pending-state announcements pass. Verify visual wrapping at phone widths in Task 4, since DOM tests do not establish layout.

- [ ] **Step 6: Commit Task 2**

```bash
git add dashboard/frontend/src/pages/ExamplesPage.tsx dashboard/frontend/src/styles.css dashboard/frontend/src/pages/ExamplesPage.test.tsx
git commit -m "feat(chat-voice): improve conversation onboarding"
```

### Task 3: Group language, response depth, composer, and voice actions

**Files:**
- Modify: `dashboard/frontend/src/pages/ExamplesPage.tsx`
- Modify: `dashboard/frontend/src/styles.css`
- Test: `dashboard/frontend/src/pages/ExamplesPage.test.tsx`

**Interfaces:**
- Consumes: Existing `ExampleLanguage`, `ExampleChatPreset`, `draft`, `recording`, `transcribing`, `transcriptReady`, and chat/speech API handlers.
- Produces: A compact visible language selector, a three-choice response-depth control, a reachable message/voice dock, and an honest recording indicator with elapsed time. Existing Danish/English and Fast/Balanced/Quality values remain unchanged.

- [ ] **Step 1: Add settings and voice interaction tests**

Test selecting each response preset and language; assert chat, transcription, and synthesis calls keep the selected values. Test that recording exposes a timer/state announcement, returns to idle after release/cancel, and that a completed transcript remains editable before send. Keep existing keyboard and pointer tests passing.

- [ ] **Step 2: Run focused tests and confirm the new assertions fail**

Run: `npm test -- src/pages/ExamplesPage.test.tsx`

Expected: the current slider and voice control do not provide the new discrete preset labels or recording elapsed-time announcement.

- [ ] **Step 3: Replace the range control with an accessible three-choice setting**

In `ExamplesPage.tsx`, render Fast, Balanced, and Quality as a labelled radio group that updates the existing `preset` state. Keep `PRESET_COPY` and the current API enum values. Keep the Danish/English selector visible beside or immediately before the response-depth group.

- [ ] **Step 4: Add a responsive composer and voice dock**

Group the text form, send action, hold-to-talk control, and speak-replies toggle under one composer dock. Add recording elapsed-time state that updates only while capture is active; show a restrained pulse and an accessible “Recording” status. Preserve the existing release, cancellation, permission, and 120-second limit behavior. Do not draw fake audio levels.

- [ ] **Step 5: Style the grouped controls and dock**

In `styles.css`, make the composer the strongest action area, keep language and response depth easy to scan, and use a sticky bottom dock with safe-area padding. Cap textarea growth so long drafts do not hide send or voice controls.

- [ ] **Step 6: Run the focused frontend test**

Run: `npm test -- src/pages/ExamplesPage.test.tsx`

Expected: all preset/language propagation, recording-state, transcript-review, and existing cancellation tests pass.

- [ ] **Step 7: Commit Task 3**

```bash
git add dashboard/frontend/src/pages/ExamplesPage.tsx dashboard/frontend/src/styles.css dashboard/frontend/src/pages/ExamplesPage.test.tsx
git commit -m "feat(chat-voice): streamline compose and voice controls"
```

### Task 4: Polish motion, accessibility, and responsive layout

**Files:**
- Modify: `dashboard/frontend/src/styles.css`
- Modify: `dashboard/frontend/src/pages/ExamplesPage.tsx` only where semantic states need a label or live region
- Test: `dashboard/frontend/src/pages/ExamplesPage.test.tsx`

**Interfaces:**
- Consumes: The workspace sections and state classes from Tasks 1–3.
- Produces: Consistent visual tokens and focus treatment, nonessential motion that respects reduced-motion preferences, and a layout that keeps all actions available at the required viewport widths.

- [ ] **Step 1: Add accessibility state assertions**

Test keyboard access to the service disclosure, language selector, response-depth group, composer, and push-to-talk action. Assert lifecycle and recording changes use accessible status text and that decorative motion elements are hidden from assistive technology.

- [ ] **Step 2: Add reduced-motion styles**

In `styles.css`, add `@media (prefers-reduced-motion: reduce)` rules that remove message entrance transitions and continuous spinner/pulse animation while preserving static visual state cues.

- [ ] **Step 3: Finish responsive hierarchy and focus styling**

Use the existing mobile breakpoint strategy. At 320–430px, keep the page single-column, avoid horizontal overflow, allow long content to wrap, preserve 48px touch targets and 16px composer text, and keep the sticky dock above the safe-area inset. At desktop widths, constrain transcript line length and keep the dock aligned with the conversation column.

- [ ] **Step 4: Run frontend automated verification**

Run from `dashboard/frontend`: `npm test`, `npm run typecheck`, and `npm run build`.

Expected: every test passes, TypeScript reports no errors, and Vite produces a production build.

- [ ] **Step 5: Inspect the workspace at representative sizes**

Use the authenticated workspace at 320px, 390px, 430px, and desktop width. Verify empty and populated conversations, long replies, the open service disclosure, recording/transcription, the mobile keyboard, and reduced-motion mode. Confirm the composer is reachable, no controls are obscured, and there is no horizontal scrolling.

- [ ] **Step 6: Commit Task 4**

```bash
git add dashboard/frontend/src/pages/ExamplesPage.tsx dashboard/frontend/src/styles.css dashboard/frontend/src/pages/ExamplesPage.test.tsx
git commit -m "style(chat-voice): polish responsive workspace states"
```

## Self-review

- **Spec coverage:** Conversation hierarchy and empty-state onboarding are in Task 2; lifecycle and dependency status are in Task 1; language, response depth, composer, and voice are in Task 3; phone sizing, motion, and accessibility are in Task 4.
- **Step scan:** Each task adds a failing behavior test, implements one coherent UI area, and ends with a focused verification and commit.
- **Type consistency:** Tasks use the existing `ExamplePublicStatus`, `ExampleLanguage`, `ExampleChatPreset`, and existing zero-argument lifecycle callbacks; no API type or contract changes are planned.
- **Review focus:** All five review conditions map to tests or manual viewport checks in their owning tasks.
- **Proportion:** Four tasks split the existing single-page UI by user-facing area without adding dependencies or changing unrelated dashboard systems.
