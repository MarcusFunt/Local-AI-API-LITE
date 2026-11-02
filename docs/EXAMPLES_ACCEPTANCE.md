# Chat + Voice acceptance

Use this checklist for a deliberate human evaluation with the local host and services available. Automated tests are model-free and use fake Docker/model/speech adapters; they verify contracts and failure handling but do not establish live model quality, speech quality, or host behavior. Do not run live inference as part of CI.

## Automated evidence

The current automated checks cover registry and Compose validation, lifecycle and activity-gate behavior, authenticated proxy boundaries, consumer request contracts, bounded inputs, and dashboard interactions. Run the repository checks listed in [DASHBOARD.md](DASHBOARD.md). Record command results and aggregate test counts in the change review; these results are separate from the host acceptance below.

## Manual host checklist

Record only readiness and aggregate measurements. Do not copy prompts, completions, transcripts, recordings, or personal audio into tickets or issue trackers.

- [ ] Sign in to the local dashboard and confirm Examples shows the current chat and voice dependency readiness.
- [ ] Start Chat + Voice. On a fresh image, record approximate build time; then confirm the card reaches Ready.
- [ ] Complete a meaningful multi-turn task using typed turns. Assess whether the answers are useful and coherent across turns.
- [ ] Submit the same non-sensitive evaluation prompt once at Fast and once at Quality. Compare usefulness, response length, and elapsed time. Treat Quality as a request preset, not a correctness guarantee.
- [ ] During a deliberately slow chat request, confirm the elapsed timer updates and the status changes to “Still working” after 10 seconds. Select **Stop waiting**; confirm the prompt returns to the composer and no unanswered turn is added to the conversation. The model may remain busy until its bounded request finishes.
- [ ] Start a recording and release to transcribe, then clear the session before STT returns. Confirm recording tracks stop, the pending transcript is discarded, and the composer stays clear.
- [ ] Hold to record a Danish utterance; release and confirm an editable transcript appears in the composer without being sent automatically.
- [ ] Edit and send the transcript, confirm the turn joins the existing conversation, play the synthesized reply, and continue with another typed or spoken turn.
- [ ] Deny microphone access and confirm typed chat remains available. Restore microphone access in browser settings and confirm recording can recover.
- [ ] Stop the example and confirm the card reports Stopped. Reload the page and confirm the previous conversation and composer content are gone.
- [ ] If Docker takes longer than the browser request deadline to stop, confirm the page reports Stopping, keeps chat unavailable, and refreshes to the controller's current state.
- [ ] If scheduled as a separate host exercise, verify the 30-minute idle stop and Docker/controller interruption recovery without capturing conversation content.

## Evidence record

Complete this record without storing any user content:

| Field | Record |
| --- | --- |
| Date and operator | |
| Host/OS and dashboard revision | |
| Docker availability | Ready / unavailable / recovered |
| `ddu-narrator` readiness | Ready / unavailable |
| `ddu-stt` and `ddu-tts` readiness | Both ready / STT unavailable / TTS unavailable / both unavailable |
| First image build duration (if applicable) | Aggregate duration only |
| Fast and Quality aggregate response durations | Durations only; no prompt or reply |
| Danish transcription and synthesis outcome | Pass / fail, plus sanitized category only |
| Microphone denial and recovery | Pass / fail |
| Stop, reload, and recovery outcome | Pass / fail |
| Issues | Sanitized behavior and error category only |

Do not record prompts, replies, transcripts, audio, names, or other personal content in this table or its associated ticket.
