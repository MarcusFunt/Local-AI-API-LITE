# Local Danish speech services

The independent CPU-only speech project provides speech-to-text (STT) at `ddu-stt:8080` and text-to-speech (TTS) at `ddu-tts:8080` on the existing private Docker network `ddu_inference_bus`. It does not alter the vLLM narrator or the retrieval Compose project.

## Pinned profile

The dashboard profile registry lists `danish-speech-cpu` beside the narrator profiles. It pins:

| Service | Alias | Runtime | Model or voice | Revision |
|---|---|---|---|---|
| STT | `ddu-stt` | faster-whisper 1.2.1, CTranslate2 CPU int8 | `Systran/faster-whisper-small` | `536b0662742c02347bc0e980a01041f333bce120` |
| TTS | `ddu-tts` | piper-tts 1.8.0, ONNX Runtime CPU | `da_DK-talesyntese-medium` | `320d5f7f7751a17ef6512d5c23863056c6a11c0f` |

The STT checkpoint declares MIT in its [pinned model repository](https://huggingface.co/Systran/faster-whisper-small/tree/536b0662742c02347bc0e980a01041f333bce120). The faster-whisper runtime is pinned to [release 1.2.1](https://github.com/SYSTRAN/faster-whisper/releases/tag/v1.2.1). The Piper runtime is GPL-3.0-or-later ([release 1.8.0](https://github.com/OHF-Voice/piper1-gpl/releases/tag/v1.8.0)). The [pinned Danish voice model card](https://huggingface.co/rhasspy/piper-voices/blob/320d5f7f7751a17ef6512d5c23863056c6a11c0f/da/da_DK/talesyntese/medium/MODEL_CARD) identifies its dataset as CC0; that dataset statement is separate from the model file's terms. Review the pinned voice model card before redistributing the voice. The dashboard labels this profile `implementation-unverified` until the exact artifacts have been loaded and smoke-tested on the target host.

## Install pinned artifacts

Set `MODEL_CACHE_DIR` in `deploy/speech/.env` to the absolute external model-cache directory used by the other local model services. The installer adds its files under `hub/` and `speech/piper/`; it does not remove existing cache content. Create `ddu_inference_bus` once if needed.

From PowerShell:

```powershell
Set-Location .\deploy\speech
Copy-Item .env.example .env
# Set MODEL_CACHE_DIR in .env before continuing.
docker network inspect ddu_inference_bus *> $null
if ($LASTEXITCODE -ne 0) { docker network create ddu_inference_bus }
docker compose --profile install run --rm model-installer --plan
docker compose --profile install run --rm model-installer --install
```

The download command is explicit. Serving containers use the cache read-only and Hugging Face offline mode. The installer writes the voice repository revision and file SHA-256 values into a manifest; TTS readiness checks that manifest and both files before loading the voice.

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

Transcribe Danish audio through the OpenAI-compatible multipart endpoint:

```text
POST http://ddu-stt:8080/v1/audio/transcriptions
Content-Type: multipart/form-data

file=<audio file>
model=ddu-stt
language=da
response_format=json
```

`response_format` accepts `json`, `verbose_json`, or `text`. The service accepts Danish (`da` or `da-DK`), audio up to 25,000,000 bytes and 120 seconds, and at most two outstanding inferences. Invalid audio, oversized input, saturation, timeouts, and model failures return bounded sanitized errors.

Synthesize a WAV file through:

```http
POST http://ddu-tts:8080/v1/audio/speech
Content-Type: application/json

{"model":"ddu-tts","input":"God dag.","voice":"da_DK-talesyntese-medium","response_format":"wav"}
```

The input is limited to 4,000 characters. The model ID, voice ID, and WAV response format are fixed; unknown choices fail with 422. Both workers omit request bodies from logs and never return model exceptions or local cache paths.

The dashboard Models page shows the pins, limits, aliases, and license notes. Speech services are listed separately from narrator profiles; the narrator's lifecycle controls do not operate them.

## Acceptance still needed on the target host

The repository tests use injected model adapters and do not download or load the actual speech artifacts. After the operator installs the pins and starts the speech profile, check each service's `/health`, `/ready`, `/v1/models`, one synthetic Danish transcription, and one short WAV synthesis. Record warm latency and Docker CPU/memory independently for STT and TTS. Cold-start measurements require an operator to start the services separately; the request runner does not start or stop containers.

Run the model-free speech contract and bounded WAV decoder checks from the repository root after installing `requirements-dev.txt` plus the service's `av` and `numpy` packages:

```powershell
python -m pip install -r requirements-dev.txt av numpy
python -m pytest services/speech/tests tests/test_speech_deployment.py
```
