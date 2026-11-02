# Chat + Voice Dedicated Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the authenticated Chat + Voice example a stable catalog URL and a dedicated workspace URL that opens after the consumer becomes ready.

**Architecture:** Keep the existing global example lifecycle and React application. Add lightweight History API navigation for `/examples` and `/examples/chat-voice`, serve the SPA entry for those paths, and render the existing Chat + Voice controls in a focused workspace without the operations sidebar.

**Tech Stack:** React 19, TypeScript, Vite, FastAPI, Starlette `StaticFiles`, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-11-02-chat-voice-dedicated-page-design.md`

## Global Constraints

- Keep the dedicated page behind the existing dashboard sign-in.
- The catalog lives at `/examples`; the stable workspace path is `/examples/chat-voice`.
- Keep chat history and drafts in browser memory; reloading clears the conversation.
- Keep lifecycle ownership in the existing dashboard API and `ExampleRunner`.
- Do not add per-run URLs, server-side conversation storage, or a public unauthenticated route.
- Do not add a routing dependency.

## Review Focus

- Direct entry at `/examples/chat-voice` while signed out must keep that path through sign-in and open the workspace afterward. Test: `App.test.tsx` direct-route authentication case.
- An asynchronous start that first reports `starting` must remain in the catalog until status becomes `ready`. Test: `ExamplesPage.test.tsx` start-to-ready navigation case.
- A rejected or failed start must stay in the catalog and keep the retryable error visible. Test: `ExamplesPage.test.tsx` failed-start case.
- Browser Back and Forward must switch between catalog and workspace without desynchronizing the address bar. Test: `App.test.tsx` history-navigation case.
- Only the explicit SPA paths should return the frontend entry; unknown paths must remain 404 and API routes must continue to resolve. Test: `test_api.py` SPA-route allowlist case.

---

### Task 1: Serve the authenticated SPA on example paths

**Files:**
- Modify: `dashboard/backend/app/api.py`
- Test: `dashboard/backend/tests/test_api.py`

**Interfaces:**
- Consumes: `app.state.frontend_dist`, already configured by `create_app(frontend_dist=...)`.
- Produces: `GET /examples` and `GET /examples/chat-voice` return the built `index.html`; the existing static mount continues to serve assets and unknown paths remain 404.

- [ ] **Step 1: Add failing route tests**

Add `test_example_spa_paths_serve_index_and_unknown_path_is_404`. Create a temporary `dist/index.html` containing a unique marker, pass `frontend_dist=dist` to `create_app`, and assert both example paths return status 200 with the marker while `/not-an-app-route` returns 404.

- [ ] **Step 2: Run the focused test and confirm failure**

Run from `dashboard/backend`: `./.venv/Scripts/python.exe -m pytest tests/test_api.py::test_example_spa_paths_serve_index_and_unknown_path_is_404 -q`

Expected: the example path assertions fail because the mounted static directory has no matching file.

- [ ] **Step 3: Add explicit SPA entry routes**

In `dashboard/backend/app/api.py`, register only `/examples` and `/examples/chat-voice` as `FileResponse` routes for `frontend_dist/index.html`, before mounting `StaticFiles`. Keep API routes before these routes and keep the static mount for assets.

- [ ] **Step 4: Run the focused backend test**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_api.py::test_example_spa_paths_serve_index_and_unknown_path_is_404 -q`

Expected: both explicit paths return the marker and the unknown path returns 404.

---

### Task 2: Add URL navigation and the dedicated Chat + Voice workspace

**Files:**
- Modify: `dashboard/frontend/src/App.tsx`
- Modify: `dashboard/frontend/src/pages/ExamplesPage.tsx`
- Modify: `dashboard/frontend/src/styles.css`
- Create: `dashboard/frontend/src/App.test.tsx`
- Test: `dashboard/frontend/src/pages/ExamplesPage.test.tsx`

**Interfaces:**
- Consumes: the existing `startExample`, `getExamples`, `sendChatTurn`, `transcribeAudio`, and `synthesizeReply` APIs.
- Produces: `ExamplesPage` accepts `workspace: boolean`, `onOpenWorkspace: () => void`, and `onReturnToCatalog: () => void`; `App` keeps `window.location.pathname` and React page selection synchronized through `pushState` and `popstate`.

- [ ] **Step 1: Add failing frontend route and lifecycle tests**

In `App.test.tsx`, cover a signed-out direct visit to `/examples/chat-voice`, successful sign-in preserving that URL and displaying the workspace, and Back/Forward between `/examples` and `/examples/chat-voice`. In `ExamplesPage.test.tsx`, cover waiting on a `starting` status before calling `onOpenWorkspace`, and verify a rejected start does not call it and shows the start error.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run from `dashboard/frontend`: `npm test -- src/App.test.tsx src/pages/ExamplesPage.test.tsx`

Expected: the route tests fail because `App` has no path-based navigation and `ExamplesPage` has no workspace callbacks.

- [ ] **Step 3: Implement route synchronization in `App.tsx`**

Use `/examples` and `/examples/chat-voice` as the explicit example paths. Initialize the selected page from the current path, retain the path while authentication is pending, push history entries for dashboard and example navigation, and restore page/path from `popstate`. Keep the same `ExamplesPage` instance mounted when moving from the catalog to its workspace so in-memory chat state survives that route change.

- [ ] **Step 4: Implement start-to-ready navigation and workspace presentation**

In `ExamplesPage.tsx`, track whether the current catalog start should open the workspace. Navigate only when the status reaches `ready`; clear that intent on start failure. In workspace mode, render a focused header with a return-to-catalog link and existing status/Start/Stop controls, then the existing chat and voice controls. Preserve the current catalog presentation outside workspace mode.

- [ ] **Step 5: Add full-page and mobile workspace styles**

In `styles.css`, hide the dashboard sidebar and operations topbar only in workspace mode, let the workspace use the available viewport width, and retain a single-column layout and touch-sized controls at the existing mobile breakpoint.

- [ ] **Step 6: Run focused frontend tests**

Run: `npm test -- src/App.test.tsx src/pages/ExamplesPage.test.tsx`

Expected: route, authentication, history, ready-transition, and failed-start cases pass.

- [ ] **Step 7: Run frontend verification**

Run: `npm test`, `npm run typecheck`, and `npm run build`.

Expected: the complete frontend suite passes, TypeScript reports no errors, and Vite produces the production build.

---

## Self-review

- Spec coverage: sign-in, stable route, direct loading/reload, Back/Forward, existing lifecycle controls, catalog return, in-memory conversation, and the single-consumer lifecycle are assigned to Tasks 1 and 2.
- Step scan: each task separates failing tests, their expected failure, implementation, and verification.
- Type consistency: `ExamplesPage` callbacks are zero-argument navigation functions; workspace mode is a boolean; route strings are `/examples` and `/examples/chat-voice`.
- Review focus: all five listed input classes have an owning test above.
- Proportion: two tasks cover one backend fallback and one frontend route/workspace change; no routing library or persistence layer is introduced.
