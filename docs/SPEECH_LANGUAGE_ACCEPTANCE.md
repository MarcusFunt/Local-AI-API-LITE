# Danish and English speech smoke acceptance

**Date:** 2026-11-02 (Europe/Copenhagen)  
**Scope:** Local live smoke test of the pinned speech models through the Chat + Voice consumer. This verifies language routing and audio I/O; it is not a pronunciation review, cold-start benchmark, or sustained-load acceptance.

## Installation and model identity

- Built `model-installer` from the current checkout before asking it to plan or install.
- The no-write `--plan` reported STT `Systran/faster-whisper-small@536b0662742c02347bc0e980a01041f333bce120` and Piper voices from `rhasspy/piper-voices@375a0fe641dea077c2a47b4e9a056d6da521eed3`, with an estimated 700 MiB cache allocation.
- Ran the reviewed install command. It completed with `Pinned Danish and English speech artifacts installed.` The cache manifest records per-file SHA-256 digests.
- Rebuilt the speech image and recreated only `stt` and `tts`. `ddu-narrator` remained running and was not restarted or changed.

| Alias | Model and revision | `/ready` | Languages / voice mapping |
|---|---|---:|---|
| `ddu-stt` | `Systran/faster-whisper-small` / `536b0662742c02347bc0e980a01041f333bce120` | 200 | `da`, `en` |
| `ddu-tts` | `rhasspy/piper-voices` / `375a0fe641dea077c2a47b4e9a056d6da521eed3` | 200 | `da` → `da_DK-talesyntese-medium`; `en` → `en_US-ljspeech-medium` |

Both `/v1/models` responses reported those aliases, revisions, languages, and mappings. The installed `speech/piper/manifest.json` lists hashes for each voice's ONNX model and JSON configuration.

## Live language paths

Built and temporarily started the current Chat + Voice consumer on its existing loopback-only port, using an ephemeral test token, then removed that consumer after the run. The test asked the narrator for an exact Danish phrase and an exact English phrase. Both replies contained their requested language marker. Each reply was synthesized by the selected Piper voice and sent to transcription with the same language code.

| Language | Chat | Synthesis | WAV verification | Transcription |
|---|---:|---:|---|---|
| Danish (`da`) | 200; requested-language marker present | 200 | `audio/wav`, RIFF/WAVE, mono, 22,050 Hz, 1.83 s | 200; nonempty transcript (31 characters) |
| English (`en`) | 200; requested-language marker present | 200 | `audio/wav`, RIFF/WAVE, mono, 22,050 Hz, 1.97 s | 200; nonempty transcript (31 characters) |

## Memory and OOM observations

The TTS container's configured memory limit is 1 GiB. Before the bilingual rebuild it reported 232.9 MiB while the Danish-only runtime was idle. After loading both voices, it reported 236.1 MiB idle and 318.7 MiB after the two live synthesis requests (about 31% of the limit). `docker inspect` reported `OOMKilled=false` and zero restarts. STT reported 549.7 MiB of its 4 GiB limit after loading. These are observed Docker stats samples, not a measured peak under sustained or concurrent load.
