import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";

import {
  getExamples,
  sendChatTurn,
  startExample,
  stopExample,
  synthesizeReply,
  transcribeAudio,
} from "../api";
import type {
  ExampleChatMessage,
  ExampleChatPreset,
  ExampleLanguage,
  ExamplePublicStatus,
  ExampleState,
} from "../api";

const MAX_RECORDING_MS = 120_000;
const MAX_AUDIO_BYTES = 25_000_000;
const MAX_TTS_CHARACTERS = 4000;
const CHAT_PRESETS: ExampleChatPreset[] = ["fast", "balanced", "quality"];
const STARTER_PROMPTS = [
  "Explain how a local AI model works.",
  "Help me plan a small coding project.",
  "Give me three ideas to try in this chat.",
];
const PRESET_COPY: Record<ExampleChatPreset, string> = {
  fast: "Shorter answers, prioritizes response time.",
  balanced: "The default tradeoff between response time and answer depth.",
  quality: "More generation budget and the model's supported thinking mode; can take longer.",
};
const STATE_LABELS: Record<ExampleState, string> = {
  stopped: "Stopped",
  starting: "Preparing",
  ready: "Ready",
  stopping: "Stopping",
  failed: "Failed",
  "recovery-required": "Recovery required",
};
const FAILURE_COPY: Record<string, string> = {
  docker_unavailable: "Docker is unavailable. Check Docker Desktop and refresh status.",
  build_timeout: "The first image build timed out. Check Docker and retry the start.",
  build_failed: "The example image could not be built. Check Docker's image build status and retry.",
  start_timeout: "The example did not start before the deadline. Refresh status and retry.",
  start_failed: "The example could not be started. Check Docker and retry.",
  unhealthy: "The example did not become healthy. Check Docker and the dashboard status.",
  stop_failed: "The example may still be running. Refresh status to reconcile it.",
  recovery_required: "The controller needs to confirm the container state before another start.",
};

interface ConversationMessage extends ExampleChatMessage {
  elapsedMs?: number;
  id: number;
}

interface ActiveChatRequest {
  controller: AbortController;
  content: string;
  previousMessages: ConversationMessage[];
}

function apiStatus(error: unknown): number | null {
  if (typeof error === "object" && error !== null && "status" in error) {
    const status = (error as { status?: unknown }).status;
    return typeof status === "number" ? status : null;
  }
  return null;
}

function formatDate(value: string | null): string {
  if (!value) return "Unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Unavailable"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatElapsed(milliseconds: number): string {
  const totalSeconds = Math.floor(Math.max(0, milliseconds) / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function StateBadge({ state, announce = false }: { state: ExampleState; announce?: boolean }) {
  return <span className={`state-badge state-${state}`} role={announce ? "status" : undefined} aria-live={announce ? "polite" : undefined}><span className="state-dot" />{STATE_LABELS[state]}</span>;
}

function DependencyRow({ title, detail, state }: { title: string; detail: string; state: string }) {
  return <div className="example-dependency-row">
    <div><strong>{title}</strong><span>{detail}</span></div>
    <span className={`example-dependency-state example-dependency-${state}`}>{state}</span>
  </div>;
}

export function ExamplesPage({ csrfToken, onSessionExpired, workspace, onOpenWorkspace, onReturnToCatalog }: {
  csrfToken: string;
  onSessionExpired: () => void;
  workspace: boolean;
  onOpenWorkspace: () => void;
  onReturnToCatalog: () => void;
}) {
  const [status, setStatus] = useState<ExamplePublicStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [pendingAction, setPendingAction] = useState<"start" | "stop" | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [preset, setPreset] = useState<ExampleChatPreset>("balanced");
  const [language, setLanguage] = useState<ExampleLanguage>("da");
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [chatStartedAt, setChatStartedAt] = useState<number | null>(null);
  const [chatElapsedMs, setChatElapsedMs] = useState(0);
  const [transcribing, setTranscribing] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordingStartedAt, setRecordingStartedAt] = useState<number | null>(null);
  const [recordingElapsedMs, setRecordingElapsedMs] = useState(0);
  const [transcriptReady, setTranscriptReady] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [voiceNotice, setVoiceNotice] = useState<string | null>(null);
  const [synthesizing, setSynthesizing] = useState<number | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [playAudioOnLoad, setPlayAudioOnLoad] = useState(false);
  const [speakReplies, setSpeakReplies] = useState(false);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);

  const mountedRef = useRef(false);
  const messageInputRef = useRef<HTMLTextAreaElement | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const transcriptNearEndRef = useRef(true);
  const previousMessagesRef = useRef<ConversationMessage[]>([]);
  const focusComposerAfterDraftRef = useRef(false);
  const pointerHeldRef = useRef(false);
  const cancelStopRef = useRef(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const stopRecordingRef = useRef<() => void>(() => undefined);
  const streamRef = useRef<MediaStream | null>(null);
  const microphoneTrackListenersRef = useRef<Array<{ track: MediaStreamTrack; listener: () => void }>>([]);
  const chunksRef = useRef<Blob[]>([]);
  const stopTimerRef = useRef<number | null>(null);
  const activeChatRef = useRef<ActiveChatRequest | null>(null);
  const transcribeAbortRef = useRef<AbortController | null>(null);
  const synthAbortRef = useRef<AbortController | null>(null);
  const audioUrlRef = useRef<string | null>(null);
  const audioElementRef = useRef<HTMLAudioElement | null>(null);
  const messageIdRef = useRef(0);
  const openWorkspaceOnReadyRef = useRef(false);
  const statusRequestIdRef = useRef(0);

  useLayoutEffect(() => {
    if (!focusComposerAfterDraftRef.current) return;
    focusComposerAfterDraftRef.current = false;
    const input = messageInputRef.current;
    if (!input) return;
    input.focus();
    const caret = input.value.length;
    input.setSelectionRange(caret, caret);
  }, [draft]);

  useLayoutEffect(() => {
    if (!workspace) return;
    const input = messageInputRef.current;
    if (!input) return;
    input.style.height = "auto";
    if (input.scrollHeight > 0) {
      input.style.height = `${Math.min(input.scrollHeight, Math.min(180, window.innerHeight * 0.25))}px`;
    }
  }, [draft, workspace]);

  const setAudio = useCallback((nextUrl: string | null) => {
    if (audioUrlRef.current && audioUrlRef.current !== nextUrl) {
      URL.revokeObjectURL(audioUrlRef.current);
    }
    audioUrlRef.current = nextUrl;
    setAudioUrl(nextUrl);
  }, []);

  const refresh = useCallback(async (): Promise<ExamplePublicStatus | null> => {
    const requestId = ++statusRequestIdRef.current;
    setStatusError(null);
    try {
      const examples = await getExamples();
      const next = examples[0] ?? null;
      if (!mountedRef.current || requestId !== statusRequestIdRef.current) return null;
      setStatus(next);
      setStatusError(null);
      setLoading(false);
      return next;
    } catch (cause) {
      if (apiStatus(cause) === 401) onSessionExpired();
      if (mountedRef.current && requestId === statusRequestIdRef.current) {
        setStatusError("Example status is unavailable. Try refreshing the page status.");
        setLoading(false);
      }
      return null;
    }
  }, [onSessionExpired]);

  const releaseTracks = useCallback(() => {
    microphoneTrackListenersRef.current.forEach(({ track, listener }) => track.removeEventListener?.("ended", listener));
    microphoneTrackListenersRef.current = [];
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const abortChatRequest = useCallback((restorePrompt: boolean) => {
    const request = activeChatRef.current;
    if (!request) return;
    activeChatRef.current = null;
    request.controller.abort();
    if (!mountedRef.current) return;
    if (restorePrompt) {
      setMessages(request.previousMessages);
      setDraft(request.content);
    }
    setSending(false);
    setChatStartedAt(null);
    setChatElapsedMs(0);
  }, []);

  const abortTranscriptionRequest = useCallback(() => {
    const request = transcribeAbortRef.current;
    transcribeAbortRef.current = null;
    request?.abort();
    if (mountedRef.current) setTranscribing(false);
  }, []);

  const clearRecordingTimer = useCallback(() => {
    if (stopTimerRef.current !== null) {
      window.clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }
  }, []);

  const cancelRecording = useCallback((showNotice = true) => {
    pointerHeldRef.current = false;
    clearRecordingTimer();
    cancelStopRef.current = true;
    const recorder = recorderRef.current;
    recorderRef.current = null;
    if (recorder && recorder.state !== "inactive") {
      try { recorder.stop(); } catch { /* A recorder that already stopped has no retained audio. */ }
    }
    chunksRef.current = [];
    releaseTracks();
    if (mountedRef.current) {
      setRecording(false);
      if (showNotice) setVoiceNotice("Recording cancelled; no audio was sent.");
    }
  }, [clearRecordingTimer, releaseTracks]);

  const abortRequests = useCallback(() => {
    abortChatRequest(false);
    abortTranscriptionRequest();
    synthAbortRef.current?.abort();
    synthAbortRef.current = null;
  }, [abortChatRequest, abortTranscriptionRequest]);

  useEffect(() => {
    mountedRef.current = true;
    void refresh();
    return () => {
      mountedRef.current = false;
      abortRequests();
      cancelRecording(false);
      setAudio(null);
    };
  }, [abortRequests, cancelRecording, refresh, setAudio]);

  useEffect(() => {
    if (!status) return undefined;
    const hidden = document.visibilityState === "hidden";
    const delay = hidden
      ? 30_000
      : status.state === "starting" || status.state === "stopping"
        ? 5_000
        : status.state === "ready" ? 15_000 : 30_000;
    const timer = window.setTimeout(() => { void refresh(); }, delay);
    return () => window.clearTimeout(timer);
  }, [refresh, status]);

  useEffect(() => {
    if (!openWorkspaceOnReadyRef.current || !status) return;
    if (status.state === "ready") {
      openWorkspaceOnReadyRef.current = false;
      if (!workspace) onOpenWorkspace();
    } else if (["failed", "recovery-required", "stopped", "stopping"].includes(status.state)) {
      openWorkspaceOnReadyRef.current = false;
    }
  }, [onOpenWorkspace, status, workspace]);

  useEffect(() => {
    if (chatStartedAt === null) return undefined;
    const updateElapsed = () => setChatElapsedMs(Math.max(0, Date.now() - chatStartedAt));
    const timer = window.setInterval(updateElapsed, 1000);
    return () => window.clearInterval(timer);
  }, [chatStartedAt]);

  useEffect(() => {
    const previousMessages = previousMessagesRef.current;
    const appended = messages.length > previousMessages.length || (
      messages.length > 0
      && messages.length >= previousMessages.length
      && messages[messages.length - 1]?.id !== previousMessages[previousMessages.length - 1]?.id
    );
    previousMessagesRef.current = messages;

    const transcript = transcriptRef.current;
    if (!transcript) return;
    if (messages.length === 0) {
      transcriptNearEndRef.current = true;
      setShowJumpToLatest(false);
      return;
    }
    if (!appended) return;
    if (transcriptNearEndRef.current) {
      transcript.scrollTop = transcript.scrollHeight;
      setShowJumpToLatest(false);
    } else {
      setShowJumpToLatest(true);
    }
  }, [messages]);

  useEffect(() => {
    if (!recording || recordingStartedAt === null) return undefined;
    const timer = window.setInterval(() => setRecordingElapsedMs(Math.max(0, Date.now() - recordingStartedAt)), 1000);
    return () => window.clearInterval(timer);
  }, [recording, recordingStartedAt]);

  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [refresh]);

  useEffect(() => {
    if (!recording) return undefined;
    const releaseOnWindow = () => stopRecordingRef.current();
    const cancelOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") cancelRecording();
    };
    const cancelOnInterruption = () => cancelRecording();
    const cancelWhenHidden = () => {
      if (document.visibilityState === "hidden") cancelOnInterruption();
    };
    window.addEventListener("pointerup", releaseOnWindow);
    const cancelOnWindow = () => cancelRecording();
    window.addEventListener("pointercancel", cancelOnWindow);
    window.addEventListener("blur", cancelOnInterruption);
    document.addEventListener("visibilitychange", cancelWhenHidden);
    window.addEventListener("keydown", cancelOnEscape);
    return () => {
      window.removeEventListener("pointerup", releaseOnWindow);
      window.removeEventListener("pointercancel", cancelOnWindow);
      window.removeEventListener("blur", cancelOnInterruption);
      document.removeEventListener("visibilitychange", cancelWhenHidden);
      window.removeEventListener("keydown", cancelOnEscape);
    };
  }, [cancelRecording, recording]);

  const voiceReady = status?.voice_dependency.state === "ready";
  const appReady = status?.state === "ready";
  const canChat = appReady && !sending && !transcribing && !recording && !pendingAction;
  const canUseVoice = appReady && voiceReady && !pendingAction && !sending && !transcribing;

  const handleError = useCallback((cause: unknown, action: "status" | "start" | "stop" | "chat" | "voice") => {
    const code = apiStatus(cause);
    if (code === 401) {
      onSessionExpired();
      return "Your dashboard session expired. Sign in again.";
    }
    if (code === 403) return "Request verification failed. Refresh the dashboard and try again.";
    if (code === 409 && action === "start") {
      if (status?.chat_dependency.state !== "ready") {
        return "ddu-narrator is not ready. Start it from Models before opening this example.";
      }
      return "Another dashboard activity is active. Finish or stop it before opening the example.";
    }
    if (code === 503 && action === "start") return "Docker or the example service is unavailable. Check Docker and refresh status.";
    if (code === 409 && action === "chat") return "The example is not ready for requests. Refresh its status.";
    if (cause instanceof Error && cause.name === "AbortError") return "The request was cancelled.";
    if (action === "voice") return "The speech service is unavailable. Check that both speech services are ready.";
    if (action === "status") return "Example status is unavailable. Try refreshing status.";
    return `The example ${action} request could not be completed. Refresh status and try again.`;
  }, [onSessionExpired, status]);

  const start = async () => {
    if (pendingAction || status?.chat_dependency.state !== "ready") return;
    // Observations requested before this lifecycle action must not overwrite it.
    statusRequestIdRef.current++;
    setPendingAction("start");
    setError(null);
    setNotice(null);
    try {
      const next = await startExample(csrfToken);
      openWorkspaceOnReadyRef.current = !workspace;
      setStatus(next);
      setMessages([]);
      setDraft("");
      setTranscriptReady(false);
      await refresh();
    } catch (cause) {
      openWorkspaceOnReadyRef.current = false;
      const current = await refresh();
      setError(handleError(cause, "start"));
      if (current?.state === "recovery-required") setError("The controller needs to confirm the example container is stopped before retrying.");
    } finally {
      setPendingAction(null);
    }
  };

  const stop = async () => {
    if (pendingAction) return;
    statusRequestIdRef.current++;
    openWorkspaceOnReadyRef.current = false;
    cancelRecording(false);
    abortRequests();
    setMessages([]);
    setDraft("");
    setTranscriptReady(false);
    setAudio(null);
    setPlayAudioOnLoad(false);
    setError(null);
    setNotice(null);
    setVoiceNotice(null);
    setVoiceError(null);
    setPendingAction("stop");
    setStatus((current) => current ? { ...current, state: "stopping" } : current);
    try {
      setStatus(await stopExample(csrfToken));
      setNotice("The example stop was requested. The activity reservation stays until Docker confirms it stopped.");
      await refresh();
    } catch (cause) {
      const current = await refresh();
      setError(handleError(cause, "stop"));
      if (current?.state === "recovery-required") setError("The controller needs to confirm the example container is stopped before another start.");
    } finally {
      setPendingAction(null);
    }
  };

  const clearConversation = () => {
    cancelRecording(false);
    abortChatRequest(false);
    abortTranscriptionRequest();
    synthAbortRef.current?.abort();
    setMessages([]);
    setDraft("");
    setTranscriptReady(false);
    setAudio(null);
    setPlayAudioOnLoad(false);
    setError(null);
    setNotice("This in-memory conversation was cleared.");
    setVoiceError(null);
    setVoiceNotice(null);
  };

  const submitMessage = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const content = draft.trim();
    if (!content || !canChat) return;
    const requestLanguage = language;
    const previousMessages = messages;
    transcriptNearEndRef.current = true;
    setShowJumpToLatest(false);
    const history: ExampleChatMessage[] = [
      ...messages.slice(-15).map(({ role, content: messageContent }) => ({ role, content: messageContent })),
      { role: "user", content },
    ];
    const userMessage: ConversationMessage = { id: ++messageIdRef.current, role: "user", content };
    setMessages((previous) => [...previous.slice(-15), userMessage]);
    setDraft("");
    setTranscriptReady(false);
    setSending(true);
    setError(null);
    setNotice(null);
    const controller = new AbortController();
    const activeRequest: ActiveChatRequest = { controller, content, previousMessages };
    activeChatRef.current = activeRequest;
    setChatStartedAt(Date.now());
    setChatElapsedMs(0);
    try {
      const reply = await sendChatTurn(csrfToken, requestLanguage, preset, history, controller.signal);
      const assistantMessage: ConversationMessage = {
        id: ++messageIdRef.current,
        role: "assistant",
        content: reply.text,
        elapsedMs: reply.elapsed_ms,
      };
      if (mountedRef.current && !controller.signal.aborted && activeChatRef.current === activeRequest) {
        setMessages((previous) => [...previous.slice(-15), assistantMessage]);
        if (speakReplies && voiceReady && reply.text.length <= MAX_TTS_CHARACTERS) {
          void speak(reply.text, assistantMessage.id, requestLanguage, true);
        }
      }
    } catch (cause) {
      if (mountedRef.current && !controller.signal.aborted && activeChatRef.current === activeRequest) {
        setMessages(previousMessages);
        setDraft(content);
        setError(`${handleError(cause, "chat")} Your message is back in the composer so you can retry.`);
      }
    } finally {
      if (activeChatRef.current === activeRequest) {
        activeChatRef.current = null;
        if (mountedRef.current) {
          setSending(false);
          setChatStartedAt(null);
          setChatElapsedMs(0);
        }
      }
    }
  };

  const cancelChat = () => {
    if (!activeChatRef.current) return;
    abortChatRequest(true);
    setError(null);
    setNotice("Stopped waiting. Your message is back in the composer. The local model may finish this request in the background.");
  };

  const transcribe = useCallback(async (audio: Blob, recordingLanguage: ExampleLanguage) => {
    if (!audio.size || audio.size > MAX_AUDIO_BYTES) {
      setVoiceError(audio.size > MAX_AUDIO_BYTES ? "Recording exceeded the 25 MB audio limit." : "No audio was recorded. Hold to talk and try again.");
      return;
    }
    setTranscribing(true);
    setVoiceError(null);
    setVoiceNotice(null);
    const controller = new AbortController();
    transcribeAbortRef.current = controller;
    try {
      const result = await transcribeAudio(csrfToken, audio, recordingLanguage, controller.signal);
      if (!mountedRef.current || controller.signal.aborted) return;
      if (!result.text.trim()) {
        setDraft("");
        setTranscriptReady(false);
        setVoiceNotice("No speech detected. Hold to talk and try again.");
      } else {
        setDraft((previous) => {
          const prefix = previous.trimEnd();
          return prefix ? `${prefix}\n${result.text}` : result.text;
        });
        setTranscriptReady(true);
        setVoiceNotice("Transcript ready. Review and edit it in the composer, then press Send.");
      }
    } catch (cause) {
      if (mountedRef.current && !controller.signal.aborted) setVoiceError(handleError(cause, "voice"));
    } finally {
      if (transcribeAbortRef.current === controller) {
        transcribeAbortRef.current = null;
        if (mountedRef.current) setTranscribing(false);
      }
    }
  }, [csrfToken, handleError, onSessionExpired]);

  const stopRecording = useCallback(() => {
    pointerHeldRef.current = false;
    clearRecordingTimer();
    const recorder = recorderRef.current;
    if (!recorder || recorder.state === "inactive") {
      releaseTracks();
      return;
    }
    setRecording(false);
    try {
      recorder.stop();
      releaseTracks();
    } catch {
      cancelRecording(false);
      setVoiceError("Recording ended unexpectedly. Try again.");
    }
  }, [cancelRecording, clearRecordingTimer, releaseTracks]);
  stopRecordingRef.current = stopRecording;

  const beginRecording = async () => {
    if (!canUseVoice || pointerHeldRef.current) return;
    const recordingLanguage = language;
    pointerHeldRef.current = true;
    setVoiceError(null);
    setVoiceNotice(null);
    try {
      if (window.isSecureContext === false) {
        pointerHeldRef.current = false;
        setVoiceError("Microphone recording requires HTTPS. Open this page from the Tailscale HTTPS address and allow microphone access in your browser's site settings.");
        return;
      }
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
        pointerHeldRef.current = false;
        setVoiceError("This browser does not support push-to-talk audio recording.");
        return;
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mountedRef.current || !pointerHeldRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      const mimeType = ["audio/webm", "audio/mp4"].find((type) => MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      recorderRef.current = recorder;
      chunksRef.current = [];
      cancelStopRef.current = false;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        recorderRef.current = null;
        releaseTracks();
        const wasCancelled = cancelStopRef.current;
        cancelStopRef.current = false;
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
        chunksRef.current = [];
        if (!wasCancelled && mountedRef.current) void transcribe(blob, recordingLanguage);
      };
      recorder.onerror = () => {
        cancelRecording(false);
        setVoiceError("Recording ended unexpectedly. Check microphone access and try again.");
      };
      for (const track of stream.getTracks()) {
        const onDeviceEnded = () => {
          cancelRecording(false);
          if (mountedRef.current) setVoiceError("Microphone disconnected. Check the device and try again.");
        };
        track.addEventListener?.("ended", onDeviceEnded, { once: true });
        microphoneTrackListenersRef.current.push({ track, listener: onDeviceEnded });
      }
      recorder.start();
      setRecordingStartedAt(Date.now());
      setRecordingElapsedMs(0);
      setRecording(true);
      stopTimerRef.current = window.setTimeout(stopRecording, MAX_RECORDING_MS);
    } catch {
      pointerHeldRef.current = false;
      releaseTracks();
      setRecording(false);
      setVoiceError("Microphone access was denied. Allow microphone access for this site in your browser settings, then try again.");
    }
  };

  const speak = useCallback(async (text: string, messageId: number, speechLanguage: ExampleLanguage, autoplay = false) => {
    if (!voiceReady || text.length > MAX_TTS_CHARACTERS || synthesizing !== null) return;
    setSynthesizing(messageId);
    setVoiceError(null);
    const controller = new AbortController();
    synthAbortRef.current = controller;
    try {
      const audio = await synthesizeReply(csrfToken, text, speechLanguage, controller.signal);
      if (!mountedRef.current || controller.signal.aborted) return;
      setAudio(URL.createObjectURL(audio));
      setPlayAudioOnLoad(autoplay);
      if (autoplay) setNotice("Spoken reply is ready.");
    } catch (cause) {
      if (mountedRef.current && !controller.signal.aborted) setVoiceError(handleError(cause, "voice"));
    } finally {
      if (synthAbortRef.current === controller) synthAbortRef.current = null;
      if (mountedRef.current) setSynthesizing(null);
    }
  }, [csrfToken, handleError, onSessionExpired, synthesizing, setAudio, voiceReady]);

  const canStart = status?.chat_dependency.state === "ready"
    && status.activity_available
    && (status.state === "stopped" || status.state === "failed")
    && pendingAction === null;
  const canStop = status !== null
    && ["ready", "starting", "failed", "recovery-required"].includes(status.state)
    && pendingAction === null;
  const statusSummary = useMemo(() => {
    if (!status) return "The fixed Chat + Voice consumer app runs separately from inference.";
    if (status.chat_dependency.state !== "ready") return "ddu-narrator is not ready. Start it from Models before opening this example.";
    if (status.state === "recovery-required") return FAILURE_COPY[status.last_error_category ?? "recovery_required"];
    if (status.state === "failed") return FAILURE_COPY[status.last_error_category ?? "start_failed"];
    if (status.state === "starting") return "The first start may build the pinned app image. This can take a few minutes.";
    if (status.state === "ready") return "Chat is ready. Speech controls become available when both speech services are ready.";
    return "The example never starts or reconfigures inference or speech services.";
  }, [status]);

  const chatDependencyDetail = status?.chat_dependency.state === "ready"
    ? `Ready${status.chat_dependency.active_profile ? ` · ${status.chat_dependency.active_profile}` : ""}`
    : status?.chat_dependency.state === "unknown" ? "Readiness is unknown" : "Start ddu-narrator from Models";
  const voiceDependencyDetail = voiceReady
    ? "ddu-stt and ddu-tts are ready"
    : "Voice is unavailable; chat remains available";

  const stopAudio = () => {
    if (audioElementRef.current) {
      audioElementRef.current.pause();
      audioElementRef.current.currentTime = 0;
    }
  };

  const selectStarterPrompt = (prompt: string) => {
    focusComposerAfterDraftRef.current = prompt !== draft;
    setDraft(prompt);
    const input = messageInputRef.current;
    input?.focus();
    if (prompt === draft) input?.setSelectionRange(prompt.length, prompt.length);
  };

  const handleTranscriptScroll = () => {
    const transcript = transcriptRef.current;
    if (!transcript) return;
    transcriptNearEndRef.current = transcript.scrollHeight - transcript.clientHeight - transcript.scrollTop <= 80;
    if (transcriptNearEndRef.current) setShowJumpToLatest(false);
  };

  const jumpToLatest = () => {
    const transcript = transcriptRef.current;
    if (!transcript) return;
    transcript.scrollTop = transcript.scrollHeight;
    transcriptNearEndRef.current = true;
    setShowJumpToLatest(false);
  };

  const recordingStatus = <div className={`recording-state${recording ? " recording-state-active" : ""}`}>
    {recording && <span className="recording-dot" aria-hidden="true" />}
    <span role="status" aria-label="Voice input status" aria-live="polite">{recording ? "Recording" : transcribing ? "Transcribing speech…" : voiceError ? "Voice needs attention" : voiceReady ? canUseVoice ? "Ready to record" : sending ? "Voice controls paused during chat" : "Speech services ready" : "Voice unavailable"}</span>
    {recording && <span role="timer" aria-label={`Recording elapsed time ${formatElapsed(recordingElapsedMs)}`} aria-live="off">{formatElapsed(recordingElapsedMs)} / 02:00</span>}
  </div>;

  const pushToTalkButton = <button
    className={`secondary-button push-to-talk ${recording ? "push-to-talk-recording" : ""}`}
    type="button"
    disabled={!canUseVoice || transcribing}
    aria-label={transcribing ? "Transcribing speech" : recording ? "Release to transcribe" : "Hold to talk"}
    aria-describedby={workspace ? "voice-guidance" : undefined}
    onPointerDown={(event) => { event.preventDefault(); void beginRecording(); }}
    onPointerUp={() => stopRecording()}
    onPointerCancel={() => cancelRecording()}
    onKeyDown={(event) => {
      if ((event.key === " " || event.key === "Enter") && !event.repeat) {
        event.preventDefault();
        void beginRecording();
      }
    }}
    onKeyUp={(event) => {
      if (event.key === " " || event.key === "Enter") stopRecording();
    }}
    onBlur={() => { if (recording) cancelRecording(); }}
  ><svg className="microphone-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <rect x="9" y="3" width="6" height="12" rx="3" />
      <path d="M6 11a6 6 0 0 0 12 0M12 17v4m-4 0h8" />
    </svg>{transcribing ? "Transcribing…" : recording ? "Release to transcribe" : "Hold to talk"}</button>;

  const showStartAction = !workspace || canStart || pendingAction === "start" || status?.state === "stopped" || status?.state === "failed";
  const showStopAction = !workspace || canStop || pendingAction === "stop" || status?.state === "stopping";

  const startAction = showStartAction && <button className="primary-button" type="button" disabled={!canStart || loading} onClick={() => void start()}>
    {pendingAction === "start" ? "Starting…" : status?.state === "failed" ? "Retry start" : "Start example"}
  </button>;
  const stopAction = showStopAction && <button className="secondary-button" type="button" disabled={!canStop} onClick={() => void stop()}>
    {pendingAction === "stop" || status?.state === "stopping" ? "Stopping…" : "Stop example"}
  </button>;
  const refreshAction = <button className="text-button" type="button" disabled={loading} onClick={() => void refresh()}>Refresh status</button>;
  const lifecycleActions = <div className="example-actions">
    {!workspace && appReady && <a className="workspace-open primary-button" href="/examples/chat-voice" onClick={(event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      onOpenWorkspace();
    }}>Open Chat + Voice</a>}
    {startAction}
    {stopAction}
    {refreshAction}
  </div>;
  const primaryRecoveryAction = workspace && (status?.state === "stopped" || status?.state === "failed") ? startAction : null;
  const workspaceOperations = <div className="example-actions workspace-operational-actions">
    {!primaryRecoveryAction && startAction}
    {stopAction}
    {refreshAction}
  </div>;

  const serviceDetails = <>
    {(!workspace || !status?.last_error_category) && <p className="example-summary">{statusSummary}</p>}
    <div className="example-dependencies">
      <DependencyRow title="Chat model" detail={chatDependencyDetail} state={status?.chat_dependency.state ?? "unknown"} />
      <DependencyRow title="Speech services" detail={voiceDependencyDetail} state={status?.voice_dependency.state ?? "unknown"} />
    </div>
    {status?.state === "ready" && <div className="example-idle-note">Idle stop: {formatDate(status.idle_expires_at)} · Stops after 30 minutes without app requests.</div>}
    {status?.started_at && status.state !== "stopped" && <div className="example-started-note">Started {formatDate(status.started_at)}</div>}
    {status && !status.activity_available && status.state !== "ready" && <div className="example-started-note">Another dashboard activity is active. Finish it before starting this example.</div>}
  </>;
  const recoveryGuidance = status?.last_error_category && <p className="example-error" role="status">{FAILURE_COPY[status.last_error_category] ?? "The example needs recovery. Refresh status before retrying."}</p>;

  return <div className={`page-content examples-content${workspace ? " example-workspace" : ""}`}>
    {!workspace && <section className="intro-panel examples-intro">
      <span className="eyebrow">ONE LOCAL HANDS-ON EXAMPLE</span>
      <h2>Try real tasks with your local model</h2>
      <p>Chat stays in this browser session. Push-to-talk is turn based: record, review the transcript, then send it with the conversation.</p>
    </section>}

    <section className="panel example-card" aria-label={workspace ? "Chat + Voice workspace" : undefined} aria-labelledby={workspace ? undefined : "example-title"}>
      <div className={workspace ? "workspace-header" : undefined}>
        {workspace && <a className="workspace-return" href="/examples" onClick={(event) => {
          if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          onReturnToCatalog();
        }}>Return to Examples</a>}
        <div className="example-card-heading">
          <div><span className="eyebrow">{workspace ? "LOCAL WORKSPACE" : "EXAMPLE 01"}</span>{workspace ? <h1 id="example-title">Chat + Voice</h1> : <h2 id="example-title">Chat + Voice</h2>}</div>
          {status ? <StateBadge state={status.state} announce={workspace} /> : <span className="state-badge state-unknown" role={workspace ? "status" : undefined} aria-live={workspace ? "polite" : undefined}>Loading</span>}
          {workspace && appReady && <span className="workspace-readiness" aria-label="Service readiness">Chat ready · {voiceReady ? "Voice ready" : "Voice unavailable"}</span>}
        </div>
        {workspace && primaryRecoveryAction && <div className="workspace-recovery-action">{primaryRecoveryAction}</div>}
      </div>
      {workspace ? <details className="workspace-service-details">
        <summary>Services and status details</summary>
        <div className="workspace-service-content">{workspaceOperations}{serviceDetails}</div>
      </details> : serviceDetails}
      {statusError && <p className="example-error" role="alert">{statusError}</p>}
      {error && <p className="example-error" role="alert">{error}</p>}
      {notice && <p className="example-notice" role="status">{notice}</p>}
      {workspace && recoveryGuidance}
      {!workspace && recoveryGuidance}
      {!workspace && lifecycleActions}
    </section>

    {appReady && <div
      className={workspace ? "workspace-conversation" : undefined}
      role={workspace ? "region" : undefined}
      aria-label={workspace ? "Chat + Voice conversation" : undefined}
    ><section className="panel chat-panel" aria-labelledby="conversation-title">
      <div className="chat-panel-heading">
        <div><span className="eyebrow">IN-MEMORY CONVERSATION</span><h2 id="conversation-title">Conversation</h2></div>
        <button className="text-button" type="button" onClick={clearConversation} disabled={messages.length === 0 && !draft && !transcribing && !recording}>Clear session</button>
      </div>

      <div className="chat-settings" role={workspace ? "group" : undefined} aria-label={workspace ? "Conversation settings" : undefined}>
      <div className="language-control">
        <label htmlFor="example-language">Language</label>
        <select id="example-language" value={language} onChange={(event) => setLanguage(event.currentTarget.value as ExampleLanguage)}>
          <option value="da">Danish</option>
          <option value="en">English</option>
        </select>
        <span>Chat, transcription, and spoken replies</span>
      </div>

      <fieldset className="preset-control" role="radiogroup">
        <legend>Response depth</legend>
        <div className="preset-options">
          {CHAT_PRESETS.map((choice) => <label className="preset-option" key={choice}>
            <input type="radio" name="example-preset" value={choice} checked={preset === choice} onChange={() => setPreset(choice)} />
            <span>{choice[0].toUpperCase() + choice.slice(1)}</span>
          </label>)}
        </div>
        <details className="preset-help">
          <summary>About response depth</summary>
          <p id="preset-description">{PRESET_COPY[preset]} This setting changes latency and answer depth. It does not guarantee correctness.</p>
        </details>
      </fieldset>
      {workspace && <label className="speak-replies-toggle"><input type="checkbox" checked={speakReplies} onChange={(event) => setSpeakReplies(event.currentTarget.checked)} disabled={!voiceReady} /> Speak replies automatically</label>}
      </div>

      <div
        className="conversation-log"
        ref={transcriptRef}
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        aria-label="Conversation"
        onScroll={handleTranscriptScroll}
      >
        {messages.length === 0
          ? <div className="conversation-empty">
            <h3>Start a conversation</h3>
            <p>Choose a prompt or type below. This session clears when you reload.</p>
            <div className="conversation-starters" role="group" aria-label="Starter prompts">
              {STARTER_PROMPTS.map((prompt) => <button
                className="conversation-starter"
                type="button"
                key={prompt}
                disabled={!canChat}
                onClick={() => selectStarterPrompt(prompt)}
              >{prompt}</button>)}
            </div>
          </div>
          : messages.map((message) => <article className={`conversation-message conversation-${message.role}`} aria-label={message.role === "user" ? "Your message" : "Assistant reply"} key={message.id}>
            <div className="conversation-message-meta">{message.role === "user" ? "YOU" : "Ddu-narrator"}</div>
            <p>{message.content}</p>
            {message.role === "assistant" && <div className="assistant-actions">
              {message.elapsedMs !== undefined && <span>{message.elapsedMs} ms</span>}
              <button
                className="text-button"
                type="button"
                disabled={!voiceReady || message.content.length > MAX_TTS_CHARACTERS || synthesizing === message.id}
                onClick={() => void speak(message.content, message.id, language, true)}
              >{synthesizing === message.id ? "Preparing audio…" : "Speak reply"}</button>
              {message.content.length > MAX_TTS_CHARACTERS && <span>Reply exceeds the 4,000 character speech limit.</span>}
            </div>}
          </article>)}
      </div>

      {sending && <div className="chat-request-status">
        <span className="chat-request-spinner" aria-hidden="true" />
        <div className="chat-request-copy">
          <p role="status" aria-live="polite">
            {chatElapsedMs >= 10_000 ? "Still working on your reply." : "Waiting for a complete reply from the local model."}
          </p>
          <span role="timer" aria-label={`Elapsed time ${formatElapsed(chatElapsedMs)}`} aria-live="off">{formatElapsed(chatElapsedMs)}</span>
        </div>
        <button className="text-button" type="button" onClick={cancelChat}>Stop waiting</button>
      </div>}

      {audioUrl && <div className="spoken-reply">
        <div className="spoken-reply-heading"><span>Spoken reply</span><button className="text-button" type="button" onClick={stopAudio}>Stop playback</button></div>
        <audio
          ref={audioElementRef}
          controls
          aria-label="Spoken reply audio"
          autoPlay={playAudioOnLoad || speakReplies}
          src={audioUrl}
          onEnded={() => { setAudio(null); setPlayAudioOnLoad(false); }}
        />
      </div>}

      <section className="composer-dock" aria-label="Message and voice composer">
      {showJumpToLatest && <button className="transcript-jump" type="button" aria-label="Jump to latest" onClick={jumpToLatest}>Jump to latest</button>}
      <form className={`example-composer${workspace ? " workspace-composer" : ""}`} onSubmit={(event) => void submitMessage(event)}>
        <label htmlFor="example-message">Message</label>
        {transcriptReady && <p className="transcript-hint" role="status">Transcript ready. Review and edit it here before sending.</p>}
        <textarea
          ref={messageInputRef}
          id="example-message"
          aria-label="Message"
          aria-describedby={workspace ? "message-limit" : undefined}
          maxLength={16_000}
          rows={workspace ? 2 : 4}
          value={draft}
          placeholder="Ask a question or describe a real task…"
          onChange={(event) => setDraft(event.currentTarget.value)}
          disabled={!canChat}
        />
        {workspace && <span className="workspace-visually-hidden" id="message-limit">Maximum length is 16,000 characters.</span>}
        {workspace ? <div className="composer-action-row">
          <div className="voice-action-group">{pushToTalkButton}{recordingStatus}</div>
          {draft.length >= 14_000 && <span className="composer-character-count" aria-live="off">{draft.length.toLocaleString()} / 16,000</span>}
          <button className="primary-button" type="submit" disabled={!canChat || !draft.trim()}>
            {sending ? "Sending…" : "Send message"}
          </button>
        </div> : <div className="composer-footer">
          <span>{draft.length.toLocaleString()} / 16,000 characters</span>
          <div><button className="primary-button" type="submit" disabled={!canChat || !draft.trim()}>{sending ? "Sending…" : "Send message"}</button></div>
        </div>}
      </form>

      {workspace
        ? <p id="voice-guidance" className="workspace-voice-guidance">{voiceReady
          ? `Hold to talk in ${language === "da" ? "Danish" : "English"}; release to add an editable transcript. Up to 120 seconds.`
          : `${voiceDependencyDetail}.`}</p>
        : <div className="voice-controls">
          <div className="voice-control-copy"><strong>Turn-based {language === "da" ? "Danish" : "English"} voice input</strong><span>{voiceDependencyDetail}. Recording stops when you release or after 120 seconds.</span></div>
          {recordingStatus}
          {pushToTalkButton}
          <label className="speak-replies-toggle"><input type="checkbox" checked={speakReplies} onChange={(event) => setSpeakReplies(event.currentTarget.checked)} disabled={!voiceReady} /> Speak replies automatically</label>
        </div>}
      {voiceError && <p className="example-error" role="alert">
        {voiceError}
        {window.isSecureContext === false && window.location.hostname.endsWith(".ts.net") && <> <a href={`https://${window.location.host}${window.location.pathname}${window.location.search}`}>Open the Tailscale HTTPS page</a></>}
      </p>}
      {voiceNotice && <p className="example-notice" role="status">{voiceNotice}</p>}
      </section>
    </section></div>}

    {!workspace && status?.state === "ready" && !voiceReady && <p className="example-voice-note">Chat is ready while push-to-talk and spoken replies wait for both ddu-stt and ddu-tts.</p>}
  </div>;
}
