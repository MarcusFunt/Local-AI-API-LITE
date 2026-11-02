# Chat + Voice Language Selection Design

## Intent

Let a user choose Danish or English once in Chat + Voice and have the selected language govern the narrator reply, speech transcription, and spoken reply. This makes the page behave consistently for users who work in either language.

## Goals

- Add one clear language selector with Danish and English choices.
- Pass the selected `da` or `en` code through each consumer request to its inference service.
- Instruct `ddu-narrator` to answer in the selected language.
- Transcribe with the selected language and synthesize with its matching pinned voice.
- Keep language selection in page memory; default to Danish after reload.
- Preserve the existing authenticated dashboard boundary, service aliases, privacy behavior, and bounded request/resource limits.

## Non-goals

- Automatic language detection, translation, mixed-language voice settings, or adding languages beyond Danish and English.
- Changing the served narrator model, speech runtime, or dashboard authentication and hosting.
- Launching inference services automatically or persisting language preference or conversation content.

## Approaches

1. **One end-to-end selector (selected).** A single language state is sent to chat, STT, and TTS. This directly matches the requested behavior and keeps the interface simple.
2. **Separate chat and voice languages.** Offers mixed-language workflows but adds more controls and lets the services disagree about the active language.
3. **Automatic detection.** Chooses a language from speech and may change reply behavior without an explicit user choice, which is less predictable than the requested selector.

## Design calls

- The selector offers Danish (`da`) and English (`en`) and defaults to Danish. Changing it affects subsequent chat, transcription, manual TTS, and automatic TTS requests. It does not clear the existing in-memory conversation; reloading resets the selector to Danish as it does other page state.
- The consumer's strict request contracts allow only `da` and `en`. Chat receives a language-specific system instruction. Transcription forwards the same code to `ddu-stt`; synthesis forwards it to `ddu-tts`. Unsupported values fail validation rather than silently falling back to Danish.
- `ddu-stt` continues using the existing multilingual faster-whisper-small model, extending its accepted language allowlist to `da` and `en` and passing the chosen code to the adapter.
- `ddu-tts` keeps its existing Danish voice and adds the pinned English US voice `en_US-ljspeech-medium` from `rhasspy/piper-voices` revision `375a0fe641dea077c2a47b4e9a056d6da521eed3`. Update the TTS revision pin to that snapshot; the Danish model files retain their existing hashes there. The English model file is 63,531,379 bytes at that revision; its model card identifies it as English (United States), medium quality, trained from scratch on the public-domain LJ Speech dataset. The voice installer and manifest must pin and verify both voices. Document the voice model card's dataset terms separately from the repository and runtime licenses before installing the English artifact.
- Voice selection is a fixed language-to-voice mapping inside the service. Clients cannot submit arbitrary voice IDs or model paths. Readiness must indicate whether both selected-language voices are installed and usable, and model loading/caching must respect the existing TTS memory and concurrency limits.
- Existing authenticated same-origin APIs remain the only browser boundary. Language codes are request metadata; prompts, transcripts, audio, and replies remain transient and are never added to logs or persistent storage.

## Constraints and verification

- Reuse the current React, FastAPI, faster-whisper, and Piper stack; add no runtime dependency.
- Test that both language codes propagate through consumer and worker contracts, that each TTS language selects only its pinned voice, that invalid codes fail safely, and that the UI sends the selected language for every inference action.
- Run affected frontend, consumer, speech-worker, installer, and dashboard checks; rebuild the relevant local services and verify Danish and English configuration through their existing aliases. Live quality and pronunciation remain an operator acceptance check.

## Primary model references

- [Pinned Piper English voice files](https://huggingface.co/rhasspy/piper-voices/tree/375a0fe641dea077c2a47b4e9a056d6da521eed3/en/en_US/ljspeech/medium)
- [Pinned Piper English voice model card](https://huggingface.co/rhasspy/piper-voices/blob/375a0fe641dea077c2a47b4e9a056d6da521eed3/en/en_US/ljspeech/medium/MODEL_CARD)
- [Pinned Piper voice registry](https://huggingface.co/rhasspy/piper-voices/blob/375a0fe641dea077c2a47b4e9a056d6da521eed3/voices.json)
- [Lessac dataset research-use terms that ruled out the initial voice choice](https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html)
- [faster-whisper transcription language parameter](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py)
