# Chat + Voice Language Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Danish/English selector whose selected language controls narrator replies, speech transcription, and spoken replies.

**Architecture:** The dashboard page owns an in-memory `da`/`en` value and includes it in every authenticated consumer request. The consumer adds a language instruction to `ddu-narrator` requests and forwards the same code to `ddu-stt` and `ddu-tts`; speech workers validate that code and select only fixed pinned artifacts. The STT model stays multilingual, while the TTS installer adds the English Piper voice beside the existing Danish voice.

**Tech Stack:** React, TypeScript, FastAPI, Pydantic, faster-whisper, Piper, Docker Compose, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-02-chat-voice-language-selection-design.md`

## Global Constraints

- Support Danish (`da`) and English (`en`); default the selector to Danish and keep it in page memory only.
- Reuse the current React, FastAPI, faster-whisper, and Piper stack; add no runtime dependency.
- Preserve the existing authenticated dashboard boundary, service aliases, privacy behavior, and bounded request/resource limits.
- Unsupported values fail validation rather than silently falling back to Danish.
- Voice selection is a fixed language-to-voice mapping inside the service; clients cannot submit arbitrary voice IDs or model paths.
- Prompts, transcripts, audio, and replies remain transient and are never added to logs or persistent storage.
- Do not launch inference services automatically or persist language preference or conversation content.

## Review Focus

- A missing, malformed, or unsupported language must fail safely instead of selecting a different language. Pin this in consumer API and worker contract tests in Tasks 1 and 2.
- English and Danish must select only their corresponding pinned TTS voice; a missing or hash-mismatched artifact must not fall back to the other voice. Pin this in worker and installer tests in Tasks 2 and 3.
- Changing the selector during an in-flight chat or recording must not relabel that request; its language is captured when the action starts. Pin this in the page tests in Task 5.
- Switching languages must preserve the in-memory conversation while the next chat uses the newly selected language. Pin this in Task 5.
- The English voice's model card dataset terms must be shown separately from repository/runtime licenses, and documented before downloading the artifact. Review and pin the exact source and license note in Tasks 3 and 4.

---

### Task 1: Add language to authenticated consumer contracts

**Files:**
- Create: `examples/chat-voice/app/languages.py`
- Modify: `examples/chat-voice/app/chat.py`
- Modify: `examples/chat-voice/app/speech.py`
- Modify: `examples/chat-voice/app/main.py`
- Test: `examples/chat-voice/tests/test_chat.py`
- Test: `examples/chat-voice/tests/test_speech.py`
- Test: `examples/chat-voice/tests/test_app.py`

**Interfaces:**
- Produces `Language = Literal["da", "en"]` and `SUPPORTED_LANGUAGES = frozenset({"da", "en"})` in `languages.py`.
- Produces `ChatRequest.language: Language`, `SpeechService.transcribe(audio: bytes, content_type: str, language: Language) -> dict[str, str]`, and `SpeechService.synthesize(text: str, language: Language) -> tuple[bytes, str]`.
- The consumer chat JSON and synthesis JSON require `language`; transcription multipart form requires `file` and `language` and rejects duplicate/extra fields.

- [ ] **Step 1: Write failing consumer tests**
  - Add `test_chat_adds_selected_language_instruction` for `da` and `en`, asserting the fixed model, existing preset settings, and the appropriate final system instruction.
  - Add `test_chat_rejects_unsupported_language` for `fr` and `da-DK`.
  - Add parametrized `test_transcription_forwards_selected_language` and `test_synthesis_forwards_selected_language` for both supported codes; assert the exact request fields sent to the fixed aliases.
  - Update `test_api_routes_chat_transcription_and_synthesis_through_fixed_services` to require explicit language on all three routes; add `test_api_rejects_missing_or_unsupported_language` for chat, multipart transcription, and synthesis.
  - Preserve and exercise the existing authentication, CSRF-proxy token, size, and content-free logging assertions.
- [ ] **Step 2: Run the consumer tests and observe the contract failures**

Run: `python -m pytest examples/chat-voice/tests/test_chat.py examples/chat-voice/tests/test_speech.py examples/chat-voice/tests/test_app.py -q`

Expected: FAIL because the consumer request models and service methods do not yet accept or forward language.

- [ ] **Step 3: Implement the consumer language contract**
  - Define the shared `Language` alias and allowlist in `languages.py`.
  - Add fixed Danish and English system prompts in `chat.py`; insert the selected prompt before the existing conversation history without altering the fixed model or presets.
  - Add required language fields to the consumer chat/synthesis requests and parse transcription's exact two-field multipart form, rejecting unsupported or duplicated language fields with sanitized validation errors.
  - Forward `language` to `ddu-stt` and `ddu-tts`; remove the consumer's hard-coded Danish STT value and single TTS voice value.
- [ ] **Step 4: Run all consumer tests**

Run: `python -m pytest examples/chat-voice/tests -q`

Expected: PASS, including existing bounds, auth, timeout, error sanitization, and content-free logging tests.

---

### Task 2: Make the speech workers honor the language code

**Files:**
- Modify: `services/speech/speech_worker/contracts.py`
- Modify: `services/speech/speech_worker/app.py`
- Modify: `services/speech/speech_worker/models.py`
- Test: `services/speech/tests/test_speech_worker.py`
- Create: `services/speech/tests/test_models.py`

**Interfaces:**
- Produces worker-local `SpeechLanguage = Literal["da", "en"]` and fixed `TTS_VOICE_IDS: dict[SpeechLanguage, str]` with `da -> da_DK-talesyntese-medium` and `en -> en_US-ljspeech-medium`.
- STT accepts exactly `da` or `en` and calls `adapter.transcribe(audio, language=<request language>)`.
- TTS accepts `SpeechRequest(model, input, language: SpeechLanguage, response_format="wav")`; it does not accept a caller-supplied voice ID. `PiperAdapter.synthesize_wav(text: str, language: SpeechLanguage) -> bytes` selects the mapped voice.
- The loader reads `<PIPER_VOICE_DIR>/<language>/voice.onnx` and `voice.onnx.json`; the root manifest contains `repository`, `revision`, and `voices`, where each language entry contains its fixed `voice_id` and SHA-256 values for both files under `files`.

- [ ] **Step 1: Write failing worker tests**
  - Add `test_stt_forwards_each_supported_language_to_adapter` for both codes and `test_stt_rejects_unsupported_language` for `fr` and `da-DK`.
  - Add `test_tts_selects_the_fixed_voice_for_each_language`; assert each language reaches only its mapped voice and the response remains WAV.
  - Add `test_tts_rejects_unsupported_language_and_arbitrary_voice`; assert unsupported language and an extra `voice` field return 422.
  - Add `test_models_metadata_lists_both_languages_and_voice_ids` for the fixed worker model metadata.
  - Add model-adapter tests that assert both fixed voice IDs are loaded from their language-specific artifact paths and that both languages can synthesize through the adapter.
- [ ] **Step 2: Run the worker tests and observe the contract failures**

Run: `python -m pytest services/speech/tests/test_speech_worker.py services/speech/tests/test_models.py -q`

Expected: FAIL because STT hard-codes Danish and TTS accepts only the Danish voice.

- [ ] **Step 3: Implement multilingual worker contracts**
  - Set `STT_METADATA` and `TTS_METADATA` to report supported language codes and both fixed voice IDs without exposing local paths.
  - Validate exact `da`/`en` codes in each worker; pass the STT code through unchanged to faster-whisper.
  - Map TTS language to a pinned voice inside the worker; keep inference serialized by the existing queue and retain the existing 4,000-character, timeout, and 1 GB TTS resource limits.
  - Validate both voice manifest entries, config files, and SHA-256 hashes before marking TTS ready. Missing or corrupt entries keep readiness false.
- [ ] **Step 4: Run the full speech worker tests**

Run: `python -m pytest services/speech/tests -q`

Expected: PASS, including audio bounds, queue capacity, timeouts, readiness, sanitized errors, and both voice mappings.

---

### Task 3: Pin and install both voice artifacts

**Files:**
- Modify: `services/speech/speech_worker/contracts.py`
- Modify: `services/speech/speech_worker/install_models.py`
- Modify: `services/speech/tests/test_install_models.py`
- Modify: `services/speech/tests/test_models.py`
- Modify: `services/speech/tests/test_speech_worker.py`
- Modify: `services/speech/speech_worker/models.py`

**Interfaces:**
- Uses Piper revision `375a0fe641dea077c2a47b4e9a056d6da521eed3`, Danish voice `da_DK-talesyntese-medium`, and English voice `en_US-ljspeech-medium`.
- Installs to `<PIPER_VOICE_DIR>/da/voice.onnx`, `<PIPER_VOICE_DIR>/da/voice.onnx.json`, `<PIPER_VOICE_DIR>/en/voice.onnx`, and `<PIPER_VOICE_DIR>/en/voice.onnx.json`.
- Writes one root `manifest.json` with `repository`, `revision`, and a `voices` object. Each `da`/`en` entry contains its fixed `voice_id` and a `files` object with SHA-256 values keyed `voice.onnx` and `voice.onnx.json`.

- [ ] **Step 1: Write failing installer tests**
  - Update `test_install_plan_is_pinned_and_does_not_write_model_artifacts` to assert the STT pin, the new Piper revision, and both voice paths/IDs.
  - Update `test_installation_downloads_exact_revisions_and_records_voice_hashes` to fake both voice downloads and assert the per-language manifest hashes and paths.
  - Add `test_installer_rejects_missing_or_invalid_voice_file` and `test_manifest_validation_rejects_missing_or_changed_voice` for either language.
  - Update the worker metadata revision assertion to the approved Piper revision `375a0fe641dea077c2a47b4e9a056d6da521eed3`.
  - Keep tests offline by injecting fake snapshot/file download functions; do not download model artifacts in unit tests.
- [ ] **Step 2: Run installer tests and observe the failures**

Run: `python -m pytest services/speech/tests/test_install_models.py services/speech/tests/test_models.py -q`

Expected: FAIL because the installer and model verifier support one root-level Danish voice only.

- [ ] **Step 3: Implement dual-voice installation and verification**
  - Update the Piper revision constant to `375a0fe641dea077c2a47b4e9a056d6da521eed3`; keep the existing STT revision unchanged.
  - Download only the two fixed ONNX/config file pairs at that exact revision, validate their JSON sample rates, and write them under the `da`/`en` directories.
  - Write the new per-language SHA-256 manifest atomically; do not delete unrelated cache files.
  - Update the worker artifact verifier to require both exact manifest entries and hashes before readiness.
- [ ] **Step 4: Run installer and worker artifact tests**

Run: `python -m pytest services/speech/tests/test_install_models.py services/speech/tests/test_models.py services/speech/tests/test_speech_worker.py -q`

Expected: PASS, including no-write `--plan`, pin mismatch, missing voice, changed hash, and exact language-to-artifact mapping.

---

### Task 4: Update dashboard model metadata and operator guidance

**Files:**
- Modify: `dashboard/config/profiles.yaml`
- Modify: `dashboard/config/examples.yaml`
- Modify: `dashboard/backend/tests/test_speech_profiles.py`
- Modify: `dashboard/backend/tests/test_examples.py`
- Modify: `tests/test_speech_deployment.py`
- Modify: `dashboard/frontend/src/pages/ModelsPage.test.tsx`
- Modify: `docs/SPEECH_SERVING.md`
- Modify: `docs/EXAMPLES.md`

**Interfaces:**
- Keep the existing speech profile ID, aliases, Compose project/profile, and resource limits.
- Display both language codes and voice IDs in the existing string profile fields; identify the Danish voice dataset as CC0 and English LJ Speech data as public domain, separately from the Piper runtime license.

- [ ] **Step 1: Write failing metadata and documentation checks**
  - Update `test_loads_danish_speech_profile` to assert the profile describes Danish and English, both exact voice IDs, the new Piper revision, and separate per-voice dataset license notes.
  - Update `test_speech_profile_and_worker_constants_share_the_same_pinned_artifacts` to assert that both profile voice IDs and the new Piper revision match worker constants.
  - Update the `ModelsPage` test fixture and rendering test to assert both voice IDs and the license labels are visible.
  - Update `test_registry_exposes_only_the_fixed_chat_voice_example_and_limits` to assert the example description no longer describes voice input as Danish-only.
- [ ] **Step 2: Run the focused checks and observe failures**

Run: `python -m pytest dashboard/backend/tests/test_speech_profiles.py -q` and `npm --prefix dashboard/frontend test -- --run src/pages/ModelsPage.test.tsx`

Expected: FAIL because the registry and example description still identify a Danish-only voice profile.

- [ ] **Step 3: Update profile metadata and guidance**
  - Update the existing profile's language, voice IDs, Piper revision, description, and dataset license note; keep its implementation-unverified state and all resource/alias settings.
  - Update `docs/SPEECH_SERVING.md` with the new exact pin, explicit dual-voice install layout, per-voice dataset terms, language fields for both APIs, and English/Danish examples.
  - Update `docs/EXAMPLES.md` and `dashboard/config/examples.yaml` to explain that one selector controls chat, transcription, and spoken replies and resets to Danish on reload.
  - Link the pinned English model card and its dataset terms; keep the model dataset note distinct from the repository and runtime licenses.
- [ ] **Step 4: Run the focused metadata checks**

Run: `python -m pytest dashboard/backend/tests/test_speech_profiles.py -q` and `npm --prefix dashboard/frontend test -- --run src/pages/ModelsPage.test.tsx`

Expected: PASS with both voices and their separate license notes displayed.

---

### Task 5: Add the single language selector to Chat + Voice

**Files:**
- Modify: `dashboard/frontend/src/api.ts`
- Modify: `dashboard/frontend/src/api.test.ts`
- Modify: `dashboard/frontend/src/pages/ExamplesPage.tsx`
- Modify: `dashboard/frontend/src/pages/ExamplesPage.test.tsx`
- Modify: `dashboard/frontend/src/styles.css`

**Interfaces:**
- Produces `ExampleLanguage = "da" | "en"` in `api.ts`.
- Uses `sendChatTurn(csrfToken, language, preset, messages, signal?)`, `transcribeAudio(csrfToken, audio, language, signal?)`, and `synthesizeReply(csrfToken, text, language, signal?)`.
- Page state defaults to `da`. Each chat/recording captures its language at start; automatic speech for a chat reply uses that chat's captured language. Manual speech uses the currently selected language.

- [ ] **Step 1: Write failing API and page tests**
  - Add API tests in `api.test.ts` asserting the JSON/multipart requests carry the exact selected language for chat, transcription, and synthesis.
  - Add `test_language_selector_defaults_to_danish_and_preserves_conversation_when_changed`.
  - Add `test_english_selection_is_sent_to_chat_transcription_and_manual_tts`.
  - Add `test_in_flight_chat_and_recording_keep_the_language_captured_at_start`; assert automatic TTS for a chat reply reuses the chat language while later actions use the new selection.
  - Assert the selector exposes only Danish/English, reload starts at Danish, and switching does not clear history.
- [ ] **Step 2: Run the focused frontend tests and observe the failures**

Run: `npm --prefix dashboard/frontend test -- --run src/api.test.ts src/pages/ExamplesPage.test.tsx`

Expected: FAIL because the API methods and page have no language parameter or selector.

- [ ] **Step 3: Implement selector state and request propagation**
  - Add the accessible Danish/English select control with stable label `Language` and ID `example-language`; default it to Danish and do not persist it.
  - Pass the captured code to chat, transcription, and speech requests. Use the code captured at chat start for that turn's automatic TTS; use current selection for manual TTS.
  - Preserve the conversation when selection changes; use the selected code for the next chat and recording.
  - Update the voice-control copy to reflect the selected language and style the selector within the existing responsive layout.
- [ ] **Step 4: Run focused frontend tests, typecheck, and build**

Run: `npm --prefix dashboard/frontend test -- --run src/api.test.ts src/pages/ExamplesPage.test.tsx src/pages/ModelsPage.test.tsx`, `npm --prefix dashboard/frontend run typecheck`, and `npm --prefix dashboard/frontend run build`

Expected: PASS; TypeScript emits no errors and Vite creates the production build.

---

### Task 6: Full verification and local service refresh

**Files:**
- No additional source files; verify the files changed in Tasks 1–5.

**Interfaces:**
- Consumer routes remain behind the existing authenticated same-origin dashboard proxy.
- Speech aliases stay `ddu-stt` and `ddu-tts`; speech services remain CPU-only and private on `ddu_inference_bus`.

- [ ] **Step 1: Run the full model-free test suites and lint**

Run: `python -m pytest examples/chat-voice/tests services/speech/tests dashboard/backend/tests/test_speech_profiles.py -q`; `npm --prefix dashboard/frontend test`; `npm --prefix dashboard/frontend run typecheck`; `npm --prefix dashboard/frontend run build`; and `ruff check examples/chat-voice/app examples/chat-voice/tests services/speech/speech_worker services/speech/tests dashboard/backend/app/profiles.py dashboard/backend/tests/test_speech_profiles.py`.

Expected: All checks pass; tests use fakes and do not send content to live inference services.

- [ ] **Step 2: Validate both Compose configurations without starting services**

Run: `docker compose --env-file deploy/speech/.env.example --file deploy/speech/compose.yaml config --quiet` and `docker compose --file examples/compose.yaml config --quiet`.

Expected: Both commands exit successfully; speech has no host-published port and no GPU request, and the example retains its loopback-only port and external private network.

- [ ] **Step 3: Install the reviewed pinned English artifact and rebuild the speech image**

Run from `deploy/speech`: `docker compose --profile install run --rm model-installer --plan`, then `docker compose --profile install run --rm model-installer --install`, followed by `docker compose --profile danish-speech build stt tts`.

Expected: The plan prints the exact STT/Piper revisions and both voice IDs; install writes only the pinned Danish/English voice files and manifest; speech image builds successfully. The source terms and pin are already documented by Task 4 before the download occurs.

- [ ] **Step 4: Refresh and verify the local Chat + Voice services**
  - Recreate `stt` and `tts` in the existing `danish-speech` Compose profile with the rebuilt image; use the dashboard's existing lifecycle path for the `chat-voice` consumer so its controller state remains accurate. Do not start, stop, or reconfigure `ddu-narrator`.
  - Confirm `/ready` succeeds for both speech aliases and `/v1/models` reports both language codes and fixed voice IDs.
  - Through the authenticated consumer/dashboard path, send one short Danish and one English chat turn, transcribe one short utterance per language, and synthesize one short WAV per language. Confirm WAV headers and the selected-language mappings; do not record personal content in logs.
  - While both TTS voices are loaded, inspect the TTS container's peak memory and OOM state after one synthesis per language; confirm it stays healthy within the existing 1 GB limit.

Expected: Each alias is healthy and both language paths work locally. Record any live voice quality or pronunciation concern as an operator acceptance issue; automated tests do not assert speech quality.
