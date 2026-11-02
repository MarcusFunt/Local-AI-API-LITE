# Danish Speech Services Implementation Plan

> **For agentic workers:** Implement task by task using the test-first cycle. The user has asked for implementation in the current worktree.

**Goal:** Add local Danish speech-to-text and text-to-speech services, expose them through bounded private APIs, and list their pinned configuration in the dashboard profile registry.

**Architecture:** Add an independent CPU-only speech Compose project with fixed `ddu-stt` and `ddu-tts` aliases on `ddu_inference_bus`. Use faster-whisper small multilingual with CPU int8 for STT and Piper's Danish medium voice for TTS; leave vLLM and retrieval projects unchanged. Add a separate `speech_profiles` group beside the existing narrator profiles and display it in the Models page.

**Tech Stack:** Python 3.11, FastAPI, faster-whisper/CTranslate2, piper-tts/ONNX Runtime, Docker Compose, React/TypeScript.

**Spec:** User request in this chat; target host and existing deployment contracts in `docs/M3_OPERATIONAL_RELIABILITY.md` and `docs/RETRIEVAL_SERVING.md`.

## Global Constraints

- Preserve the existing vLLM, retrieval workers, image pins, Compose projects, and aliases.
- Keep speech CPU-only, private on `ddu_inference_bus`, without published host ports or Docker GPU allocation.
- Pin code, model, voice, and container identities; serving must work offline after explicit installation.
- Bound audio/text size, duration, queueing, and inference time; never log submitted audio, transcript, or synthesis text.
- Do not start, stop, or reconfigure live services as part of implementation.
- Mark the speech profile as implementation-verified only by model-free checks; do not claim host model acceptance before pinned artifacts are exercised.

## Review Focus

- Oversized or malformed audio must fail with a sanitized 4xx response before inference.
- Unknown model IDs, language codes, response formats, or voices must not select another model.
- Saturated or stuck model execution must remain bounded and observable.
- Missing or wrong-revision artifacts must keep `/ready` false while `/health` remains live.
- Docker stopped, wrong identity, published port, GPU request, or unavailable states must not appear as Ready.

---

### Task 1: Speech profile contract

**Files:** `dashboard/config/profiles.yaml`, `dashboard/backend/app/speech_profiles.py`, `dashboard/backend/tests/test_speech_profiles.py`.

- [ ] Write tests for loading the Danish speech profile, exact pinned STT/TTS identities, and rejecting duplicate IDs or unreviewed aliases.
- [ ] Run the new tests and observe expected failures.
- [ ] Add `speech_profiles` beside the existing narrator `profiles`; keep narrator matching unchanged.
- [ ] Run profile and configuration tests.

### Task 2: Bounded speech APIs

**Files:** `services/speech/speech_worker/{app.py,contracts.py,models.py,queue.py}`, `services/speech/tests/test_speech_worker.py`.

- [ ] Test `/health`, loading and failed `/ready`, `/v1/models`, bounded upload/text rejection, fixed model IDs, WAV output, transcription schema, and aggregate `/metrics` with injected model adapters.
- [ ] Run tests and observe the contract failures before implementation.
- [ ] Implement OpenAI-compatible `POST /v1/audio/transcriptions` and `POST /v1/audio/speech`, with fixed CPU models, no content logging, bounded queues, and sanitized errors.
- [ ] Run speech tests, worker tests, and lint.

### Task 3: Pinned install and independent Compose profile

**Files:** `services/speech/{Dockerfile,requirements.in,requirements.lock}`, `services/speech/speech_worker/install_models.py`, `deploy/speech/{compose.yaml,.env.example}`, `tests/test_speech_deployment.py`.

- [ ] Test source/resolved Compose contract, profile selection, aliases, CPU/memory limits, read-only serving cache, explicit installer, and no published/GPU services.
- [ ] Implement fixed model and voice revision downloads plus separate `danish-speech` and `install` Compose profiles.
- [ ] Run deployment tests and `docker compose config` against a temporary non-secret `.env`.

### Task 4: Dashboard profiles and status

**Files:** `dashboard/backend/app/api.py`, `dashboard/backend/app/status_cache.py`, `dashboard/backend/app/speech_status.py`, backend tests; `dashboard/frontend/src/api.ts`, `App.tsx`, `OverviewPage.tsx`, `ModelsPage.tsx`, tests and styles.

- [ ] Test the `/profiles` response exposes narrator and speech profile groups, and test speech Docker states and pinned identities.
- [ ] Implement read-only speech service status and show the speech profile and two services in the dashboard.
- [ ] Run dashboard backend and frontend tests, type-check, and production build.

### Task 5: Operator documentation and final verification

**Files:** `docs/SPEECH_SERVING.md`, `README.md`.

- [ ] Document the explicit voice/model installer, Compose profile commands, API limits, runtime/voice license notes, and host checks still required.
- [ ] Run the complete top-level and dashboard backend suites, frontend tests/build, Ruff, and resolved Compose validation.
