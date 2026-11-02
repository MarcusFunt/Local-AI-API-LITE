# Local Danish and English speech services

The independent CPU-only speech project provides speech-to-text (STT) at `ddu-stt:8080` and text-to-speech (TTS) at `ddu-tts:8080` on the existing private Docker network `ddu_inference_bus`. It does not alter the vLLM narrator or the retrieval Compose project.

## Pinned profile

The dashboard profile registry lists `danish-speech-cpu` beside the narrator profiles. It pins:

| Service | Alias | Runtime | Model or voice | Revision |
|---|---|---|---|---|
| STT | `ddu-stt` | faster-whisper 1.2.1, CTranslate2 CPU int8 | `Systran/faster-whisper-small` | `536b0662742c02347bc0e980a01041f333bce120` |
| TTS, Danish (`da`) | `ddu-tts` | piper-tts 1.8.0, ONNX Runtime CPU | `da_DK-talesyntese-medium` | `375a0fe641dea077c2a47b4e9a056d6da521eed3` |
| TTS, English (`en`) | `ddu-tts` | piper-tts 1.8.0, ONNX Runtime CPU | `en_US-ljspeech-medium` | `375a0fe641dea077c2a47b4e9a056d6da521eed3` |

The STT checkpoint declares MIT in its [pinned model repository](https://huggingface.co/Systran/faster-whisper-small/tree/536b0662742c02347bc0e980a01041f333bce120). The faster-whisper runtime is pinned to [release 1.2.1](https://github.com/SYSTRAN/faster-whisper/releases/tag/v1.2.1). The Piper runtime is GPL-3.0-or-later ([release 1.8.0](https://github.com/OHF-Voice/piper1-gpl/releases/tag/v1.8.0)), while the voice repository's [pinned README](https://huggingface.co/rhasspy/piper-voices/blob/375a0fe641dea077c2a47b4e9a056d6da521eed3/README.md) declares MIT repository metadata.

The voice cards describe their training datasets separately:

| Voice | Pinned model card | Dataset terms and source |
| --- | --- | --- |
| Danish talesyntese | [Danish voice card](https://huggingface.co/rhasspy/piper-voices/blob/375a0fe641dea077c2a47b4e9a056d6da521eed3/da/da_DK/talesyntese/medium/MODEL_CARD) | CC0, per the card; [dataset source](https://www.nb.no/sprakbanken/en/resource-catalogue/oai-nb-no-sbr-21/) |
| English LJSpeech | [English voice card](https://huggingface.co/rhasspy/piper-voices/blob/375a0fe641dea077c2a47b4e9a056d6da521eed3/en/en_US/ljspeech/medium/MODEL_CARD) | Public domain, per the card and [LJ Speech dataset terms](https://keithito.com/LJ-Speech-Dataset/); the card says this voice was trained from scratch |

These dataset statements do not replace the runtime, repository, or model-file terms. Review the pinned cards before redistributing voices. The dashboard keeps the profile labeled `implementation-unverified`; the profile ID and Compose profile remain `danish-speech-cpu` and `danish-speech` for existing deployments.

## Install pinned artifacts

Set `MODEL_CACHE_DIR` in `deploy/speech/.env` to the absolute external model-cache directory used by the other local model services. The installer adds its files under `hub/` and `speech/piper/`; it does not remove existing cache content. Create `ddu_inference_bus` once if needed.

From PowerShell:

```powershell
Set-Location .\deploy\speech
Copy-Item .env.example .env
# Set MODEL_CACHE_DIR in .env before continuing.
docker network inspect ddu_inference_bus *> $null
if ($LASTEXITCODE -ne 0) { docker network create ddu_inference_bus }
docker compose --profile install build model-installer
docker compose --profile install run --rm model-installer --plan
docker compose --profile install run --rm model-installer --install
```

The plan prints the exact model revisions, both voice IDs, and their file destinations. The download command is explicit. Serving containers use the cache read-only and Hugging Face offline mode. The installed voice layout is:

```text
<MODEL_CACHE_DIR>/speech/piper/
  manifest.json
  da/voice.onnx
  da/voice.onnx.json
  en/voice.onnx
  en/voice.onnx.json
```

The root manifest records `repository`, `revision`, and a `voices` entry for each `da`/`en` code, containing its fixed `voice_id` and SHA-256 values for `voice.onnx` and `voice.onnx.json`. TTS validates both bundles before loading either voice. A missing or changed artifact keeps readiness false; the service does not substitute the other language's voice. Unrelated files in the existing cache are retained.

## Start and inspect

Start and stop only the speech project from its deployment directory:

```powershell
docker compose config --quiet
docker compose --profile danish-speech up -d --build
docker compose ps
docker compose logs --tail 100 stt tts
# Stop only speech services when finished:
docker compose --profile danish-speech stop
```

The project publishes no host ports, requests no GPU, and joins only the shared private inference network. Both services expose `/health`, `/ready`, `/v1/models`, and aggregate `/metrics` within Docker. `/ready` remains false while a pinned artifact is missing, while it loads, or when its bounded inference queue is degraded. Metrics do not contain audio or text.

## API

Transcribe audio through the OpenAI-compatible multipart endpoint. For Danish:

```text
POST http://ddu-stt:8080/v1/audio/transcriptions
Content-Type: multipart/form-data

file=<audio file>
model=ddu-stt
language=da
response_format=json
```

For English, use the same endpoint with:

```text
file=<English audio file>
model=ddu-stt
language=en
response_format=json
```

`language` is required and accepts only exact `da` or `en` codes; `da-DK`, missing, empty, and unsupported values fail with 422. `response_format` accepts `json`, `verbose_json`, or `text`. The service accepts audio up to 25,000,000 bytes and 120 seconds, and at most two outstanding inferences. Invalid audio, oversized input, saturation, timeouts, and model failures return bounded sanitized errors.

Synthesize a WAV file through:

```http
POST http://ddu-tts:8080/v1/audio/speech
Content-Type: application/json

{"model":"ddu-tts","input":"God dag.","language":"da","response_format":"wav"}
```

For English, send:

```json
{"model":"ddu-tts","input":"Good day.","language":"en","response_format":"wav"}
```

The input is limited to 4,000 characters. The required language selects the fixed mapping `da -> da_DK-talesyntese-medium` or `en -> en_US-ljspeech-medium` inside the worker. The model alias and WAV response format are fixed; missing or unsupported languages and caller-supplied `voice` fields fail with 422. Both workers omit request bodies from logs and never return model exceptions or local cache paths.

The dashboard Models page shows the pins, limits, aliases, and license notes. Speech services are listed separately from narrator profiles; the narrator's lifecycle controls do not operate them.

## Acceptance still needed on the target host

The repository tests use injected model adapters and do not download or load the actual speech artifacts. After the operator installs the pins and starts the speech profile, check each service's `/health`, `/ready`, and `/v1/models`, then one synthetic transcription and short WAV synthesis in each language. Confirm both voice IDs are reported, and measure peak TTS memory with both voices loaded against its existing 1 GB limit, including the container's OOM state. Record warm latency and Docker CPU/memory independently for STT and TTS. Live voice quality and pronunciation remain operator acceptance checks. Cold-start measurements require an operator to start the services separately; the request runner does not start or stop containers.

Run the model-free speech contract and bounded WAV decoder checks from the repository root after installing `requirements-dev.txt` plus the service's `av` and `numpy` packages:

```powershell
python -m pip install -r requirements-dev.txt av numpy
python -m pytest services/speech/tests tests/test_speech_deployment.py
```
