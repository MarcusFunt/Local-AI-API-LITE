# Dashboard Chat and Voice Hands-On Example Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add a dashboard-launched, single-container chat application that lets an operator try the local model on real tasks, use push-to-talk for Danish speech input, and hear spoken replies.

**Architecture:** Add one fixed chat-voice consumer backend as a separate Docker Compose service attached to ddu_inference_bus. Its server calls the existing ddu-llm, ddu-stt, and ddu-tts APIs. The interactive screen remains in the dashboard React app and sends typed requests through authenticated dashboard routes to the loopback-only consumer backend. The browser never calls the consumer container or model services directly. The dashboard owns only the app lifecycle and request boundary: it does not start or reconfigure inference or speech services.

**Tech Stack:** Existing React/Vite dashboard, FastAPI dashboard controller, Python/FastAPI example service, Docker Compose, the current OpenAI-compatible chat/STT/TTS endpoints, and existing SQLite/DPAPI dashboard storage where lifecycle metadata requires it.

**Spec:** The user-approved product direction in this task: one small, usable chat project with push-to-talk integrated into the same conversation and a speed-to-quality slider. Platform constraints and current service contracts are in docs/DASHBOARD.md, docs/DASHBOARD_SECURITY.md, docs/SPEECH_SERVING.md, and docs/RETRIEVAL_SERVING.md in Local-AI-API-LITE.

**Target:** The Local-AI-API-LITE dashboard. This plan is stored in docs/superpowers/plans alongside the dashboard operating and security documentation.

## Global Constraints

- Keep the dashboard controller bound to 127.0.0.1:8765; every operational route requires the existing administrator session, and every mutating route requires the existing CSRF and Host/Origin checks.
- Keep ddu-llm, ddu-stt, and ddu-tts on private ddu_inference_bus with no host-published inference ports. The example is a client and must not package or start model services.
- Launch only the fixed chat-voice service from the repository-owned Compose definition. Do not accept arbitrary image names, Compose paths, service names, commands, mounts, ports, or Docker arguments from HTTP requests.
- Publish only the example backend API, at 127.0.0.1:8766, for the controller's fixed upstream. Do not serve a second browser UI from that port or let the browser call the example, model, STT, or TTS containers directly.
- Allow at most one interactive example container at a time. Reserve the shared dashboard activity gate for its active lifetime so profile changes and benchmarks cannot compete with a user session. Release the reservation only after Docker confirms the example stopped.
- Do not auto-start the example or inference services when the dashboard starts. Starting the example requires the narrator API to be ready. Chat remains available if STT/TTS are stopped; the voice controls must show why they are unavailable and become usable once both speech services are ready.
- Do not persist prompts, completions, audio, transcripts, or conversation history in dashboard SQLite, Docker logs, job artifacts, or app files. Keep conversation state in browser memory and clear it on reset/reload. Retain only bounded lifecycle and aggregate timing/error metadata.
- Speech is turn-based: the user presses and holds to record, reviews/edits the transcript, sends it through the same chat flow, then optionally hears the reply. Do not describe this version as full-duplex, streaming, interruptible calling, or real-time speech.
- Use a fixed three-stop slider (Fast, Balanced, Quality) mapped to tested request presets for the currently served ddu-narrator model. It changes generation behavior and latency/length tradeoffs; it does not change models or guarantee factual accuracy.
- Keep automated contract, unit, integration, and CI checks as the primary regression tests. The interactive app and operator checklist add human usability acceptance; they do not replace automated tests or synthetic smoke/benchmark jobs.
- Bound all app resources, request sizes, timeouts, and concurrency. Do not add a Docker socket mount, privileged mode, host filesystem access, or persistent content volume to the example container.

## Review Focus

- Unknown example IDs, unsupported options, and altered Compose fields must be rejected before any Docker command runs; pin these cases in registry and rendered-Compose tests.
- Docker missing, build/start timeout, unhealthy app, controller restart, or uncertain container termination must produce a sanitized state and keep the activity gate reserved until reconciliation proves the container stopped; cover start/stop/reconcile failure paths.
- Unauthenticated, cross-origin, bad-Host, missing-CSRF, path-traversal, and direct-proxy requests must not reach the app; cover route and proxy boundary cases.
- Oversized/invalid speech uploads, overlong text, saturated STT/TTS, and timeouts must be bounded and sanitized; cover service-contract tests at the API boundary.
- Fast/quality presets, speech unavailability, mic denial, empty transcription, and interrupted recording must remain understandable and recoverable in the UI; cover deterministic UI tests plus the manual host checklist.

---

## Product Shape

The dashboard gains an Examples destination with one card: Chat + Voice. The card reports whether the app is stopped, preparing, ready, stopping, failed, or requires recovery; it also reports whether chat and voice dependencies are ready. Starting the card launches one separate consumer backend container. Once ready, the same dashboard page exposes the interactive chat and voice controls. Stop returns the example to stopped state.

The app presents one conversation surface. Users can type, or use push-to-talk to create a transcript in the same composer. A transcript is editable before submission. Assistant replies appear as text and can be spoken with the pinned Danish TTS voice. The user can disable playback and can continue with typed or spoken follow-up turns. Conversation content lives only in browser memory.

The Speed ↔ Quality slider has three fixed stops. The initial mapping is:

| Stop | Request behavior | User-facing explanation |
|---|---|---|
| Fast | max_tokens=192; chat_template_kwargs.enable_thinking=false | Shorter answers, prioritizes response time |
| Balanced | max_tokens=384; chat_template_kwargs.enable_thinking=false | Default tradeoff |
| Quality | max_tokens=768; chat_template_kwargs.enable_thinking=true | Allows more generation budget and the model's supported thinking mode; can take longer |

The app sends model=ddu-narrator and only the allowlisted values above. It displays the selected stop and per-turn elapsed time. The UI says the slider is a latency/depth setting, not a correctness guarantee. Tests pin this mapping. If the served model or vLLM request contract changes, the mapping must be revalidated before the UI labels change.

The chat example is direct multi-turn generation. It must not claim source-grounded answers or citations unless a stable end-to-end retrieval API is separately available and deliberately integrated. The current retrieval pilot is useful platform evidence but is not silently treated as a chat API.

### User flow

1. The operator starts the narrator through the existing Models controls and starts the speech profile separately when they want voice.
2. On Examples, the operator sees dependency readiness. Start is enabled when Docker is available and the narrator is ready. If speech is unavailable, the app still starts for chat and labels push-to-talk as unavailable.
3. The first start may build the pinned local example image; the UI shows Preparing and a bounded progress/error state. Later starts use the existing local image.
4. The operator opens the app, sends a real prompt, follows up, moves the slider, and judges usefulness and latency. If voice is ready, they press and hold to record, review the transcript, send, and hear the reply.
5. The operator stops the app when finished. A 30-minute no-request idle timeout also stops the example container. No conversation content is retained.

### Out of scope for the first release

- A second independent voice app or separate voice container. Chat and voice are one example and one lifecycle.
- Full-duplex voice, streaming speech, VAD, barge-in, or calls that remain open while the user is silent.
- Automatic startup, shutdown, or profile changes for the LLM, retrieval, STT, or TTS services.
- Arbitrary model selection, user-supplied endpoints, free-form generation parameters, public/LAN access, multi-user remote auth, or Docker command access.
- Persisted chat history, automatic conversation scoring, prompt/completion collection, or claims that a higher slider setting makes answers factually correct.
- Retrieval/citation UI until a stable retrieval API is defined and its quality behavior is accepted separately.

## Proposed File Map

| File | Responsibility |
|---|---|
| dashboard/config/examples.yaml | Fixed public example metadata, Compose service/profile, required and optional service dependencies, loopback port, limits, and idle timeout. |
| examples/compose.yaml | Single chat-voice service, fixed project/network, loopback-only web binding, container hardening, resource limits, and no restart policy. |
| examples/chat-voice/Dockerfile | Build the pinned, non-root consumer image from the app source and a digest-pinned Python base. |
| examples/chat-voice/requirements.lock | Fully pinned runtime dependencies for the example service. |
| examples/chat-voice/app/main.py | HTTP API, readiness, request bounds, content-free logging, and fixed internal service wiring. |
| examples/chat-voice/app/chat.py | Fixed ddu-narrator request adapter and the three slider presets. |
| examples/chat-voice/app/speech.py | STT/TTS adapters with profile-aligned limits and sanitized errors. |
| examples/chat-voice/tests/ | Model-free HTTP contract tests for chat, STT, TTS, bounds, errors, and logging. |
| dashboard/backend/app/examples.py | Strict typed registry loader and public example status models. |
| dashboard/backend/app/example_runner.py | Fixed Compose build/start/stop/status/reconcile operations and activity-gate ownership. |
| dashboard/backend/app/example_proxy.py | Narrow authenticated request adapter from dashboard API routes to the loopback-only app service. |
| dashboard/backend/app/routes/examples.py | Session-protected list/status/start/stop routes and app proxy routes. |
| dashboard/backend/app/persistence.py | Bounded example lifecycle metadata only; no user content. |
| dashboard/backend/app/api.py | Wire the example runner, proxy, and routes into the existing controller. |
| dashboard/backend/pyproject.toml | Add a compatible HTTP client dependency only if required by the selected proxy implementation. |
| dashboard/backend/tests/test_examples.py, test_example_runner.py, test_example_proxy.py, test_example_routes.py | Registry, Compose safety, lifecycle, proxy, auth, CSRF, and recovery coverage. |
| dashboard/frontend/src/api.ts | Typed example status/dependency models and API calls. |
| dashboard/frontend/src/pages/ExamplesPage.tsx and ExamplesPage.test.tsx | Dashboard card, readiness, start/stop, unified chat/voice controls, and human evaluation surface. |
| dashboard/frontend/src/App.tsx, styles.css | Add the Examples navigation item and page styling. |
| docs/EXAMPLES.md | User instructions, privacy, prerequisites, limitations, and troubleshooting. |
| docs/EXAMPLES_ACCEPTANCE.md | Human hands-on acceptance checklist and host evidence record. |
| dashboard/README.md, docs/DASHBOARD.md, docs/DASHBOARD_SECURITY.md | Document installation/build, lifecycle, access boundary, and current checks. |

## Interfaces Between Tasks

- ExampleId is the literal value chat-voice; clients cannot submit arbitrary IDs or options.
- ExamplePublicStatus contains id, name, state, chat_dependency, voice_dependency, missing_dependencies, started_at, last_error_category, and idle_expires_at. It never contains container IDs, filesystem paths, raw command output, ports, secrets, or conversation content.
- GET /api/control/v1/examples returns { "examples": ExamplePublicStatus[] } and requires an administrator session.
- POST /api/control/v1/examples/chat-voice/start and /stop require an administrator session and CSRF. Start returns 202 while building/starting, 409 when the narrator is not ready or another activity owns the gate, and sanitized 503 when Docker is unavailable. Stop returns 202 until Docker confirms exit.
- The dashboard API provides POST /api/control/v1/examples/chat-voice/chat, /transcribe, and /synthesize. Each requires the current administrator session and CSRF token. The browser uses the existing dashboard fetch client and never receives the app's upstream address or per-run token.
- Example container upstreams are fixed by registry and validated Compose state; the proxy cannot be directed to a request-provided URL, host, port, or service.
- The controller creates a random per-run upstream token, passes it only to the fixed example container, and adds it to controller-to-container requests. The token is memory-only and never returned to the browser or stored in logs/SQLite. If the controller restarts and loses the token, the existing container is recovery-required and must be stopped before another session starts.
- Example chat payloads accept the user conversation plus one preset ID (fast, balanced, or quality). The backend translates that ID into the fixed request table above; callers cannot pass model IDs, arbitrary chat_template_kwargs, max_tokens, or endpoint URLs.
- STT uses POST http://ddu-stt:8080/v1/audio/transcriptions with model ddu-stt, language da, and a supported response format. TTS uses POST http://ddu-tts:8080/v1/audio/speech with model ddu-tts, fixed voice da_DK-talesyntese-medium, and response_format=wav.

## Implementation Tasks

### Task 1: Define the fixed example and Compose boundary

**Files:**
- Create: dashboard/config/examples.yaml
- Create: examples/compose.yaml
- Create: examples/chat-voice/Dockerfile
- Create: dashboard/backend/app/examples.py
- Test: dashboard/backend/tests/test_examples.py

**Interfaces:**
- Produces ExampleId, validated registry entries, ExampleDependencyStatus, and ExamplePublicStatus for later tasks.
- Compose service ID is exactly chat-voice; its container listens on 8080, connects to external ddu_inference_bus, and binds host UI traffic to 127.0.0.1:8766 only.

- [ ] Write registry tests for the one allowed ID, public labels, service dependency flags, fixed compose service, fixed port, resource limits, and 30-minute idle timeout.
- [ ] Write rejection tests for unknown IDs, added services, host-wide port binding, changed container port, privileged mode, devices, Docker socket/host mounts, restart policy, and unexpected command/entrypoint.
- [ ] Implement strict YAML parsing in dashboard/backend/app/examples.py; reject unknown keys and validate values before any Docker operation.
- [ ] Add the Compose project with one service, external inference network, loopback-only port mapping, non-root user, read-only root filesystem, bounded tmpfs, dropped capabilities, no-new-privileges, CPU/memory/PID bounds, and no persistent volume.
- [ ] Add a digest-pinned Python base and a locked dependency file to the app image definition; do not copy .env, model caches, or dashboard runtime data into the image.
- [ ] Run python -m pytest dashboard/backend/tests/test_examples.py -q and docker compose --file examples/compose.yaml config --quiet; both must pass without starting containers.

### Task 2: Add safe example lifecycle management

**Files:**
- Create: dashboard/backend/app/example_runner.py
- Create: dashboard/backend/app/routes/examples.py
- Modify: dashboard/backend/app/control_state.py
- Modify: dashboard/backend/app/persistence.py
- Modify: dashboard/backend/app/api.py
- Test: dashboard/backend/tests/test_example_runner.py
- Test: dashboard/backend/tests/test_example_routes.py

**Interfaces:**
- ExampleRunner.list_status() returns list[ExamplePublicStatus].
- ExampleRunner.start(example_id: ExampleId) returns ExamplePublicStatus.
- ExampleRunner.stop(example_id: ExampleId) returns ExamplePublicStatus.
- ExampleRunner.reconcile() returns list[ExamplePublicStatus].
- Runner owns only the fixed local-ai-api-lite-examples Compose project and chat-voice service.

- [ ] Add fake-Docker tests for dependency readiness, accepted start, rejected start while another job/profile operation owns the activity gate, stop, start timeout, unhealthy service, Docker unavailable, and gate release only after confirmed exit.
- [ ] Add restart/reconcile tests: a controller restart must not start the example; an existing managed container is shown as running/recovery-required and remains gated until it is safely stopped or inspected as exited.
- [ ] Add route tests for session protection, CSRF on start/stop, generic sanitized failures, missing narrator conflict, and speech-unavailable status that still permits chat.
- [ ] Add one bounded SQLite lifecycle record keyed by fixed example ID. Store state/timestamps/error category and Compose identity only; never add columns for prompts, responses, audio, or transcripts.
- [ ] Implement fixed argument-array Compose operations in a dedicated runner rather than reusing ephemeral benchmark-job semantics. Build the image on the first user start when absent, then start only the registered service with a finite build/start deadline.
- [ ] Preflight the narrator as required and STT plus TTS as an optional paired capability. Do not invoke inference profile or speech lifecycle operations from this runner.
- [ ] Reserve the existing activity gate for the active example session. Stop releases it only after Docker inspection confirms the fixed container is exited; unknown state remains fail-closed.
- [ ] Add status/start/stop API routes at the paths defined above. Start and stop request bodies must be empty or strict models with no extra fields.
- [ ] Run the focused backend tests, then python -m pytest dashboard/backend/tests -q; all must pass with fake Docker adapters and no models loaded.

### Task 3: Add narrow authenticated consumer API routes

**Files:**
- Create: dashboard/backend/app/example_proxy.py
- Modify: dashboard/backend/app/routes/examples.py
- Modify: dashboard/backend/pyproject.toml only if an HTTP client is needed at runtime
- Test: dashboard/backend/tests/test_example_proxy.py

- [ ] Test authenticated/CSRF-protected chat, transcription, and synthesis forwarding, unavailable upstream, wrong Host/Origin, missing CSRF, unsupported methods, oversized bodies, malformed multipart data, response-size limits, and timeout behavior.
- [ ] Proxy only to the registered 127.0.0.1:8766 upstream after the example is ready. Do not accept upstream URLs, ports, service names, or per-run tokens from request data.
- [ ] Strip browser-supplied Host, Authorization, cookie, and routing headers. Add only the controller-owned upstream token and fixed content headers. Never forward dashboard cookies or CSRF values to the container.
- [ ] Require the per-run upstream token in the example service; store it only in controller memory, rotate it on each launch, and clear it after confirmed stop. App routes must not disclose it or log it.
- [ ] Limit routes to the fixed typed operations: chat JSON, bounded speech multipart upload, and bounded TTS JSON. Return sanitized errors and never proxy arbitrary paths or support WebSocket upgrades.
- [ ] Add the three routes to the example router and run python -m pytest dashboard/backend/tests/test_example_proxy.py dashboard/backend/tests/test_example_routes.py -q.

### Task 4: Implement the unified Chat + Voice consumer

**Files:**
- Create: examples/chat-voice/requirements.lock
- Create: examples/chat-voice/app/main.py
- Create: examples/chat-voice/app/chat.py
- Create: examples/chat-voice/app/speech.py
- Create: examples/chat-voice/tests/test_chat.py
- Create: examples/chat-voice/tests/test_speech.py
- Create: examples/chat-voice/tests/test_app.py

- [ ] Add adapter tests using fake HTTP transports. Assert every chat request uses ddu-narrator and exactly the selected preset mapping; assert clients cannot choose another model or alter model-specific template values.
- [ ] Add tests for multi-turn message ordering, empty input, invalid preset, bounded message/history, upstream timeout, sanitized HTTP errors, and no prompt/completion in captured logs.
- [ ] Add speech tests for the pinned STT/TTS aliases and voice, editable transcript flow, exact profile-aligned limits (25,000,000 bytes and 120 seconds for input audio; 4,000 characters for TTS text), concurrency saturation, invalid media, timeout, and content-free logs.
- [ ] Implement POST /api/chat in chat.py using the OpenAI-compatible http://ddu-llm:8080/v1/chat/completions contract. Keep the system profile and endpoint fixed; return assistant text plus bounded timing/usage metadata only.
- [ ] Implement preset translation as constants: fast maps to 192 output tokens and thinking disabled; balanced maps to 384 and thinking disabled; quality maps to 768 and thinking enabled. Use chat_template_kwargs.enable_thinking only because the current Qwen/vLLM smoke contract verifies it. Do not expose internal reasoning fields as user-visible answer text.
- [ ] Implement POST /api/transcribe and POST /api/synthesize with the existing STT/TTS contracts. Return the transcript as ordinary editable text; never auto-send a transcript without user action.
- [ ] Keep browser conversation state outside this service; the service accepts one bounded chat request at a time and does not store session history.
- [ ] Do not echo request bodies into exceptions, access logs, app logs, metrics, or Docker output. Stop the app process/container after 30 minutes without app requests; do not configure Compose auto-restart.
- [ ] Run python -m pytest examples/chat-voice/tests -q; tests must use fakes and must not contact live model services.

### Task 5: Add the unified dashboard Examples and chat/voice page

**Files:**
- Create: dashboard/frontend/src/pages/ExamplesPage.tsx
- Create: dashboard/frontend/src/pages/ExamplesPage.test.tsx
- Modify: dashboard/frontend/src/api.ts
- Modify: dashboard/frontend/src/App.tsx
- Modify: dashboard/frontend/src/styles.css

- [ ] Add typed ExamplePublicStatus and ExampleDependencyStatus models plus authenticated getExamples, startExample, stopExample, sendChatTurn, transcribeAudio, and synthesizeReply functions.
- [ ] Add UI tests for stopped/starting/ready/stopping/failed/recovery-required states, narrator unavailable, voice optional, slider labels and preset selection, CSRF/network errors, duplicate start/stop prevention, microphone denial, empty transcript, and recording cancellation.
- [ ] Add Examples to dashboard navigation and render one Chat + Voice card. Show LLM and speech readiness separately, the first-build/starting state, sanitized failure text, Start/Stop controls, and idle timeout.
- [ ] When ready, show the single interactive conversation surface on this dashboard page: multi-turn text, three-stop slider, push-to-talk, transcript editing, reply playback, clear-session control, and per-turn timing. The browser calls only the dashboard API and uses the already-held in-memory CSRF token.
- [ ] Request microphone permission only after user activation; release MediaStream tracks on release, cancel, stop, route change, and errors. Do not store audio, transcript, or messages in localStorage/sessionStorage.
- [ ] Keep controls disabled for incompatible activity-gate state and refresh status without polling rapidly while the app is idle. Preserve existing authentication-expiry handling.
- [ ] Run npm test, npm run typecheck, and npm run build from dashboard/frontend; all must pass.

### Task 6: Document human acceptance and operational boundaries

**Files:**
- Create: docs/EXAMPLES.md
- Create: docs/EXAMPLES_ACCEPTANCE.md
- Modify: dashboard/README.md
- Modify: docs/DASHBOARD.md
- Modify: docs/DASHBOARD_SECURITY.md

- [ ] Document how to build/start/stop the example, required narrator and optional speech readiness, the first image build, the 30-minute idle stop, and recovery after Docker/controller interruption.
- [ ] Document privacy defaults: no prompt, transcript, audio, or reply retention; current session exists in browser memory and disappears on reload/reset; logs and dashboard metadata are content-free.
- [ ] Document the turn-based voice flow and exact limitations; do not call it real-time/full-duplex. Document the slider's preset behavior and explicitly say the Quality stop is not a correctness guarantee.
- [ ] Add a manual acceptance checklist for real use: try a meaningful multi-turn task, compare the same prompt at Fast and Quality, judge usefulness and latency, speak/edit/send a Danish utterance, hear a reply, continue the conversation, deny mic access and recover, stop the example, and confirm no content remains after reload.
- [ ] Keep automated CI evidence separate from manual host acceptance. Record host/model/speech readiness and aggregate latency only; do not ask operators to paste prompts, transcripts, or personal audio into issue trackers.
- [ ] Run Markdown lint/format checks if configured, review links/commands against current scripts, and verify docs match implementation. Do not run live inference as part of CI.

## End-to-End Definition of Done

- An authenticated operator can start one separate chat-voice consumer container from the dashboard and use its chat/voice UI in the authenticated dashboard without accessing app or model ports directly.
- A typed multi-turn conversation works through the current OpenAI-compatible model API, and the fixed slider changes the documented request behavior with visible timing.
- When speech services are ready, push-to-talk records a bounded Danish utterance, presents an editable transcript, sends it through the same chat conversation, and can play the spoken reply. Chat remains usable when speech services are stopped.
- Stopping the example releases the gate only after Docker confirms exit. Dashboard/controller restart never auto-starts it, and uncertain runtime state fails closed until reconciled.
- All requests, uploads, outputs, concurrency, memory, CPU, and idle lifetime are bounded; user content is absent from persistent storage and logs.
- Model-free automated checks pass in CI. A separate operator hands-on checklist records whether this app is genuinely useful for intended work and whether its latency/voice behavior is acceptable on the target machine.

## Plan Self-Review

- **Coverage:** Dashboard discovery, fixed-container lifecycle, authenticated typed API proxy, text chat, push-to-talk/STT, synthesized replies/TTS, speed/quality behavior, security/privacy, model-free tests, manual acceptance, and operational docs each have an owning task.
- **Task interfaces:** The registry, status payload, route paths, fixed request presets, service aliases, and proxy path are defined above and reused consistently below.
- **Review focus:** The five highest-risk input and failure classes each have regression tests assigned to the owning task.
- **Scope:** One app, one container, one UI, and no new inference lifecycle or retrieval API; realtime speech and persisted content are deferred.
