import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";

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

function StateBadge({ state }: { state: ExampleState }) {
  return <span className={`state-badge state-${state}`}><span className="state-dot" />{STATE_LABELS[state]}</span>;
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
  const [transcriptReady, setTranscriptReady] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [voiceNotice, setVoiceNotice] = useState<string | null>(null);
  const [synthesizing, setSynthesizing] = useState<number | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [playAudioOnLoad, setPlayAudioOnLoad] = useState(false);
  const [speakReplies, setSpeakReplies] = useState(false);

  const mountedRef = useRef(false);
  const pointerHeldRef = useRef(false);
  const cancelStopRef = useRef(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const stopTimerRef = useRef<number | null>(null);
  const activeChatRef = useRef<ActiveChatRequest | null>(null);
  const transcribeAbortRef = useRef<AbortController | null>(null);
  const synthAbortRef = useRef<AbortController | null>(null);
  const audioUrlRef = useRef<string | null>(null);
  const audioElementRef = useRef<HTMLAudioElement | null>(null);
  const messageIdRef = useRef(0);
  const openWorkspaceOnReadyRef = useRef(false);

  const setAudio = useCallback((nextUrl: string | null) => {
    if (audioUrlRef.current && audioUrlRef.current !== nextUrl) {
      URL.revokeObjectURL(audioUrlRef.current);
    }
    audioUrlRef.current = nextUrl;
    setAudioUrl(nextUrl);
  }, []);

  const refresh = useCallback(async (): Promise<ExamplePublicStatus | null> => {
    setStatusError(null);
    try {
      const examples = await getExamples();
      const next = examples[0] ?? null;
      if (mountedRef.current) {
        setStatus(next);
        setStatusError(null);
        setLoading(false);
      }
      return next;
    } catch (cause) {
      if (apiStatus(cause) === 401) onSessionExpired();
      if (mountedRef.current) {
        setStatusError("Example status is unavailable. Try refreshing the page status.");
        setLoading(false);
      }
      return null;
    }
  }, [onSessionExpired]);

  const releaseTracks = useCallback(() => {
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
    const onVisibility = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [refresh]);

  useEffect(() => {
    if (!recording) return undefined;
    const releaseOnWindow = () => stopRecording();
    const cancelOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") cancelRecording();
    };
    window.addEventListener("pointerup", releaseOnWindow);
    const cancelOnWindow = () => cancelRecording();
    window.addEventListener("pointercancel", cancelOnWindow);
    window.addEventListener("keydown", cancelOnEscape);
    return () => {
      window.removeEventListener("pointerup", releaseOnWindow);
      window.removeEventListener("pointercancel", cancelOnWindow);
      window.removeEventListener("keydown", cancelOnEscape);
    };
  });

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
      recorder.start();
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

  return <div className={`page-content examples-content${workspace ? " example-workspace" : ""}`}>
    {workspace && <a className="workspace-return" href="/examples" onClick={(event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      onReturnToCatalog();
    }}>Return to Examples</a>}
    {!workspace && <section className="intro-panel examples-intro">
      <span className="eyebrow">ONE LOCAL HANDS-ON EXAMPLE</span>
      <h2>Try real tasks with your local model</h2>
      <p>Chat stays in this browser session. Push-to-talk is turn based: record, review the transcript, then send it with the conversation.</p>
    </section>}

    <section className="panel example-card" aria-labelledby="example-title">
      <div className="example-card-heading">
        <div><span className="eyebrow">{workspace ? "LOCAL WORKSPACE" : "EXAMPLE 01"}</span>{workspace ? <h1 id="example-title">Chat + Voice</h1> : <h2 id="example-title">Chat + Voice</h2>}</div>
        {status ? <StateBadge state={status.state} /> : <span className="state-badge state-unknown">Loading</span>}
      </div>
      <p className="example-summary">{statusSummary}</p>
      {!workspace && <div className="example-dependencies">
        <DependencyRow title="Chat model" detail={chatDependencyDetail} state={status?.chat_dependency.state ?? "unknown"} />
        <DependencyRow title="Speech services" detail={voiceDependencyDetail} state={status?.voice_dependency.state ?? "unknown"} />
      </div>}
      {status?.state === "ready" && <div className="example-idle-note">Idle stop: {formatDate(status.idle_expires_at)} · Stops after 30 minutes without app requests.</div>}
      {status?.started_at && status.state !== "stopped" && <div className="example-started-note">Started {formatDate(status.started_at)}</div>}
      {status && !status.activity_available && status.state !== "ready" && <div className="example-started-note">Another dashboard activity is active. Finish it before starting this example.</div>}
      {statusError && <p className="example-error" role="alert">{statusError}</p>}
      {error && <p className="example-error" role="alert">{error}</p>}
      {notice && <p className="example-notice" role="status">{notice}</p>}
      {status?.last_error_category && <p className="example-error" role="status">{FAILURE_COPY[status.last_error_category] ?? "The example needs recovery. Refresh status before retrying."}</p>}
      <div className="example-actions">
        {!workspace && appReady && <a className="workspace-open primary-button" href="/examples/chat-voice" onClick={(event) => {
          if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault();
          onOpenWorkspace();
        }}>Open Chat + Voice</a>}
        <button className="primary-button" type="button" disabled={!canStart || loading} onClick={() => void start()}>
          {pendingAction === "start" ? "Starting…" : status?.state === "failed" ? "Retry start" : "Start example"}
        </button>
        <button className="secondary-button" type="button" disabled={!canStop} onClick={() => void stop()}>
          {pendingAction === "stop" || status?.state === "stopping" ? "Stopping…" : "Stop example"}
        </button>
        <button className="text-button" type="button" disabled={loading} onClick={() => void refresh()}>
          Refresh status
        </button>
      </div>
    </section>

    {appReady && <section className="panel chat-panel" aria-labelledby="conversation-title">
      <div className="chat-panel-heading">
        <div><span className="eyebrow">IN-MEMORY CONVERSATION</span><h2 id="conversation-title">Chat with ddu-narrator</h2></div>
        <button className="text-button" type="button" onClick={clearConversation} disabled={messages.length === 0 && !draft && !transcribing && !recording}>Clear session</button>
      </div>

      <div className="language-control">
        <label htmlFor="example-language">Language</label>
        <select id="example-language" value={language} onChange={(event) => setLanguage(event.currentTarget.value as ExampleLanguage)}>
          <option value="da">Danish</option>
          <option value="en">English</option>
        </select>
        <span>Chat, transcription, and spoken replies</span>
      </div>

      <div className="preset-control">
        <div className="preset-heading"><label htmlFor="example-preset">Speed ↔ Quality</label><output htmlFor="example-preset">{preset[0].toUpperCase() + preset.slice(1)}</output></div>
        <input
          id="example-preset"
          aria-label="Speed ↔ Quality"
          type="range"
          min="0"
          max="2"
          step="1"
          value={CHAT_PRESETS.indexOf(preset)}
          onChange={(event) => setPreset(CHAT_PRESETS[Number(event.currentTarget.value)])}
        />
        <div className="preset-stop-labels" aria-hidden="true"><span>Fast</span><span>Balanced</span><span>Quality</span></div>
        <p>{PRESET_COPY[preset]}</p>
        <p className="preset-disclaimer">This setting changes latency and answer depth. It does not guarantee correctness.</p>
      </div>

      <div className="conversation-log" aria-live="polite" aria-label="Conversation">
        {messages.length === 0
          ? <p className="conversation-empty">Ask a real question or describe a task to try. The conversation disappears when you reload or clear this session.</p>
          : messages.map((message) => <article className={`conversation-message conversation-${message.role}`} key={message.id}>
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

      <form className="example-composer" onSubmit={(event) => void submitMessage(event)}>
        <label htmlFor="example-message">Message</label>
        {transcriptReady && <p className="transcript-hint" role="status">Transcript ready. Review and edit it here before sending.</p>}
        <textarea
          id="example-message"
          aria-label="Message"
          maxLength={16_000}
          rows={4}
          value={draft}
          placeholder="Ask a question or describe a real task…"
          onChange={(event) => setDraft(event.currentTarget.value)}
          disabled={!canChat}
        />
        <div className="composer-footer">
          <span>{draft.length.toLocaleString()} / 16,000 characters</span>
          <div>
            <button className="primary-button" type="submit" disabled={!canChat || !draft.trim()}>
              {sending ? "Sending…" : "Send message"}
            </button>
          </div>
        </div>
      </form>

      <div className="voice-controls">
        <div className="voice-control-copy"><strong>Turn-based {language === "da" ? "Danish" : "English"} voice input</strong><span>{voiceDependencyDetail}. Recording stops when you release or after 120 seconds.</span></div>
        <button
          className={`secondary-button push-to-talk ${recording ? "push-to-talk-recording" : ""}`}
          type="button"
          disabled={!canUseVoice || transcribing}
          aria-label={transcribing ? "Transcribing speech" : recording ? "Release to transcribe" : "Push to talk"}
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
        >{transcribing ? "Transcribing…" : recording ? "Release to transcribe" : "Hold to talk"}</button>
        <label className="speak-replies-toggle"><input type="checkbox" checked={speakReplies} onChange={(event) => setSpeakReplies(event.currentTarget.checked)} disabled={!voiceReady} /> Speak replies automatically</label>
      </div>
      {voiceError && <p className="example-error" role="alert">
        {voiceError}
        {window.isSecureContext === false && window.location.hostname.endsWith(".ts.net") && <> <a href={`https://${window.location.host}${window.location.pathname}${window.location.search}`}>Open the Tailscale HTTPS page</a></>}
      </p>}
      {voiceNotice && <p className="example-notice" role="status">{voiceNotice}</p>}
    </section>}

    {status?.state === "ready" && !voiceReady && <p className="example-voice-note">Chat is ready while push-to-talk and spoken replies wait for both ddu-stt and ddu-tts.</p>}
  </div>;
}
