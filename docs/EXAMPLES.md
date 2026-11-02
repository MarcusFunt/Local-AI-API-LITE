# Dashboard Examples: Chat + Voice

Chat + Voice is a local, authenticated dashboard example for trying the currently served `ddu-narrator` model. The dashboard launches one small consumer API container; it does not launch model or speech services. Chat remains available when speech is unavailable.

## Requirements and readiness

- The dashboard controller must be running and you must be signed in as its administrator.
- Docker Desktop must be available to the same Windows account as the controller, and the external `ddu_inference_bus` network must exist.
- The `ddu-narrator` alias must be healthy for chat. Voice additionally requires both `ddu-stt` and `ddu-tts` to be healthy on that network.
- The first start builds the example image locally. Later starts reuse that image unless it has been removed.
- Only one dashboard activity can run at a time. An inference operation or benchmark may make Start unavailable until it finishes.

Open **Examples** in the dashboard and select **Start** on the Chat + Voice card. The first image build can take several minutes. The card shows preparation, readiness, dependency state, and sanitized failures. Once ready, type into the composer or use the voice controls. Select **Stop** on the card when finished. If Docker is unavailable, restore Docker Desktop and refresh the page; the controller reconciles the fixed container state before allowing another start.

The app stops after 30 minutes without a proxied app request. Chat, transcription, and synthesis requests extend the idle deadline. Reloading the dashboard does not start the app again. After a controller restart, the per-run credential is gone; if the old container is still present, the dashboard requires recovery. Use **Stop**, wait for the card to report **Stopped**, then start a new session. If Docker cannot confirm the container has stopped, the activity reservation remains held and the card reports recovery required.

## Language

Use **Language** to choose Danish (`da`) or English (`en`) for chat, transcription, and spoken replies. The same selection instructs the narrator's reply language, selects the transcription language, and chooses the matching fixed TTS voice. Danish is the initial choice after every page reload; the selection stays in page memory only.

Changing the language preserves the current conversation and composer. The next chat turn or recording uses the new selection. An action already in progress keeps the language captured when it started, including automatic speech for that chat reply. Manually speaking an existing reply uses the current selection. The selector does not start or reconfigure inference services.

## Chat and speed/quality presets

The slider has three fixed stops that select request presets for the same currently served narrator model:

| Stop | Maximum output tokens | Thinking |
| --- | ---: | --- |
| Fast | 192 | Off |
| Balanced | 384 | Off |
| Quality | 768 | On |

The selected preset changes the request's output budget and thinking mode, which can change response length and latency. It does not select another model or guarantee correctness, factuality, or usefulness. While waiting, the page shows elapsed time and changes its status after 10 seconds. Chat and speech controls pause while transcription is running or the example is stopping. **Clear session** cancels a recording or pending transcription along with clearing the conversation. If a chat request fails, its prompt returns to the composer so you can edit or retry without leaving an unanswered turn in conversation history.

**Stop waiting** ends the browser's wait and returns the prompt to the composer. The current backend uses a bounded synchronous model call, so the model may finish the request in the background and briefly remain busy; retry after it becomes available. Turn timings are shown in the browser and are not persisted. You can reset the conversation at any time. Chat history is bounded in the page's memory.

## Turn-based voice

When both speech dependencies are ready, choose Danish or English and press and hold the microphone control to record an utterance in that language. Recording ends when you release, cancel, leave the page, or reach the 120-second limit. The fixed speech service transcribes the recording in its captured language. The transcript is appended to any existing composer text; review and edit it, then explicitly send it as a chat turn. The assistant reply can optionally be synthesized with the matching fixed Danish talesyntese or English LJSpeech voice and played in the page. The exact pins, artifact layout, and separate voice dataset terms are documented in [SPEECH_SERVING.md](SPEECH_SERVING.md).

The consumer validates each uploaded file by decoding its audio stream before forwarding it to STT. It rejects unsupported/invalid audio, files over 25 MB, and decoded duration over 120 seconds.

This is a turn-based flow: record, transcribe, review/edit, send, then optionally play the reply. It is not streaming, real-time, full-duplex, or interruptible voice calling. A browser microphone permission denial does not prevent typed chat; grant access in browser settings and try again if you want to use voice. If STT or TTS is unavailable, the page identifies the missing dependency and keeps the available chat controls usable.

## Privacy

Prompts, replies, recordings, transcripts, and conversation history are not written to dashboard SQLite, app files, Docker logs, or job artifacts. The conversation and current composer exist in browser component memory and disappear when you reset or reload/navigate away from the page. Audio is sent only for the current transcription request; reply text is sent only for the current synthesis request. Do not enter information you do not want sent to the local model or speech services.

The dashboard proxy authenticates each request and keeps the app's per-run token and upstream address out of the browser. Dashboard lifecycle metadata contains only bounded state, dependency readiness, sanitized error categories, and timestamps. Per-turn elapsed time is shown in the browser and is not persisted. The consumer suppresses access logs and does not log request bodies. Docker's bounded json-file log retains operational output for the app container; it is configured with a small rotation limit, and the app avoids logging request content.

## Troubleshooting

| Card or page state | What to do |
| --- | --- |
| Chat dependency unavailable | Restore the `ddu-narrator` service and wait for its health check, then refresh Examples. |
| Voice unavailable | Chat still works. Restore both `ddu-stt` and `ddu-tts` if voice is needed. |
| Preparing takes a long time / build failed | Wait for the first local image build; check that Docker Desktop is available to the signed-in user. Retry after the card reaches a stable state. |
| Start unavailable | Wait for the other dashboard lifecycle action or benchmark to finish. The shared activity gate allows one activity at a time. |
| Recovery required / Stop failed | Restore Docker Desktop and refresh. Use Stop to reconcile the existing fixed container; start is available only after Docker confirms it is stopped. |
| Microphone denied | Use typed chat, or grant microphone permission for the local dashboard origin in browser settings and retry. |
| Transcription or synthesis failed | Check speech dependency readiness. The current turn remains editable; retry or continue with typed chat. |

For hands-on evaluation and evidence recording, follow [EXAMPLES_ACCEPTANCE.md](EXAMPLES_ACCEPTANCE.md). The dashboard's broader installation and security instructions are in [DASHBOARD.md](DASHBOARD.md) and [DASHBOARD_SECURITY.md](DASHBOARD_SECURITY.md).
