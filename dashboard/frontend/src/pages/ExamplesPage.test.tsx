/** @vitest-environment jsdom */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ExamplePublicStatus } from "../api";
import { ExamplesPage } from "./ExamplesPage";

const apiMocks = vi.hoisted(() => ({
  getExamples: vi.fn(),
  startExample: vi.fn(),
  stopExample: vi.fn(),
  sendChatTurn: vi.fn(),
  transcribeAudio: vi.fn(),
  synthesizeReply: vi.fn(),
}));

vi.mock("../api", () => apiMocks);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function makeStatus(
  state: ExamplePublicStatus["state"],
  options: { chat?: "ready" | "stopped" | "unavailable" | "unknown"; voice?: "ready" | "stopped" | "unavailable" | "unknown" } = {},
): ExamplePublicStatus {
  const chat = options.chat ?? "ready";
  const voice = options.voice ?? "ready";
  return {
    id: "chat-voice",
    name: "Chat + Voice",
    state,
    chat_dependency: { state: chat, active_profile: chat === "ready" ? "ddu-narrator" : null, phase: null, observed_at: null },
    voice_dependency: { state: voice, active_profile: null, phase: null, observed_at: null },
    missing_dependencies: [
      ...(chat === "ready" ? [] : ["ddu-narrator"]),
      ...(voice === "ready" ? [] : ["ddu-stt", "ddu-tts"]),
    ],
    started_at: state === "ready" ? "2026-11-01T12:00:00Z" : null,
    last_error_category: null,
    idle_expires_at: state === "ready" ? "2026-11-01T12:30:00Z" : null,
    activity_available: true,
  };
}

function ready() {
  apiMocks.getExamples.mockResolvedValue([makeStatus("ready")]);
  apiMocks.startExample.mockResolvedValue(makeStatus("ready"));
  apiMocks.stopExample.mockResolvedValue(makeStatus("stopped"));
  apiMocks.sendChatTurn.mockResolvedValue({ text: "A useful reply", elapsed_ms: 321, usage: {} });
  apiMocks.transcribeAudio.mockResolvedValue({ text: "" });
  apiMocks.synthesizeReply.mockResolvedValue(new Blob(["RIFF audio"], { type: "audio/wav" }));
}

describe("ExamplesPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    ready();
  });

  it.each([
    ["stopped", "Stopped"],
    ["starting", "Preparing"],
    ["ready", "Ready"],
    ["stopping", "Stopping"],
    ["failed", "Failed"],
    ["recovery-required", "Recovery required"],
  ] as const)("renders the %s lifecycle state", async (state, label) => {
    apiMocks.getExamples.mockResolvedValue([makeStatus(state)]);
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    expect(await screen.findByText(label)).toBeTruthy();
  });

  it("requires the narrator to start and shows voice as an optional paired dependency", async () => {
    apiMocks.getExamples.mockResolvedValue([makeStatus("stopped", { chat: "stopped", voice: "stopped" })]);
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);

    expect(await screen.findByText(/ddu-narrator is not ready/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start example" }).hasAttribute("disabled")).toBe(true);

    apiMocks.getExamples.mockResolvedValue([makeStatus("ready", { voice: "stopped" })]);
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    expect(await screen.findByText(/voice is unavailable/i)).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Message" }).hasAttribute("disabled")).toBe(false);
    expect(screen.getByRole("button", { name: /push to talk/i }).hasAttribute("disabled")).toBe(true);
  });

  it("uses the selected fixed preset for a multi-turn chat and displays timing", async () => {
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByRole("button", { name: "Send message" });
    fireEvent.change(screen.getByRole("slider", { name: "Speed ↔ Quality" }), { target: { value: "2" } });
    expect(screen.getByText("Quality", { selector: "output" })).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Compare two ideas" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByText("A useful reply")).toBeTruthy();
    expect(screen.getByText("321 ms")).toBeTruthy();
    await waitFor(() => expect(apiMocks.sendChatTurn).toHaveBeenCalled());
    expect(apiMocks.sendChatTurn.mock.calls[0].slice(0, 3)).toEqual([
      "csrf-value",
      "quality",
      [{ role: "user", content: "Compare two ideas" }],
    ]);
    expect(screen.getByText(/does not guarantee correctness/i)).toBeTruthy();
  });

  it("restores a failed prompt to the composer so retry does not include an unanswered turn", async () => {
    apiMocks.sendChatTurn
      .mockRejectedValueOnce(new Error("temporary network failure"))
      .mockResolvedValueOnce({ text: "Recovered reply", elapsed_ms: 123, usage: {} });
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByRole("button", { name: "Send message" });

    const composer = screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: "Please keep this prompt" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByRole("alert")).toBeTruthy();
    await waitFor(() => expect(composer.value).toBe("Please keep this prompt"));
    expect(within(document.querySelector(".conversation-log") as HTMLElement).queryByText("Please keep this prompt")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText("Recovered reply")).toBeTruthy();
    expect(apiMocks.sendChatTurn.mock.calls[1][2]).toEqual([
      { role: "user", content: "Please keep this prompt" },
    ]);
  });

  it("shows live elapsed time and changes to still working after ten seconds", async () => {
    let resolveChat: ((result: { text: string; elapsed_ms: number; usage: Record<string, number> }) => void) | undefined;
    apiMocks.sendChatTurn.mockImplementation(() => new Promise((resolve) => { resolveChat = resolve; }));
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByRole("button", { name: "Send message" });
    vi.useFakeTimers();

    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "A slow request" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(screen.getByText("Waiting for a complete reply from the local model.")).toBeTruthy();
    expect(screen.getByRole("timer", { name: "Elapsed time 00:00" })).toBeTruthy();
    await act(async () => { vi.advanceTimersByTime(10_000); });
    expect(screen.getByText("Still working on your reply.")).toBeTruthy();
    expect(screen.getByRole("timer", { name: "Elapsed time 00:10" })).toBeTruthy();

    await act(async () => {
      resolveChat?.({ text: "Finished", elapsed_ms: 10_000, usage: {} });
      await Promise.resolve();
    });
    expect(screen.getByText("Finished")).toBeTruthy();
    expect(screen.queryByRole("timer", { name: "Elapsed time 00:10" })).toBeNull();
  });

  it("cancels a chat request and retries without duplicating its pending turn", async () => {
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByRole("button", { name: "Send message" });
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Earlier turn" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("A useful reply");

    let requestSignal: AbortSignal | undefined;
    apiMocks.sendChatTurn.mockImplementation((_csrf, _preset, _messages, signal) => new Promise((_resolve, reject) => {
      requestSignal = signal;
      signal.addEventListener("abort", () => {
        const error = new Error("aborted");
        error.name = "AbortError";
        reject(error);
      }, { once: true });
    }));
    const composer = screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: "Retry this turn" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    fireEvent.click(await screen.findByRole("button", { name: "Stop waiting" }));

    expect(requestSignal?.aborted).toBe(true);
    expect(composer.value).toBe("Retry this turn");
    expect(screen.getByText("Earlier turn")).toBeTruthy();
    expect(screen.getByText("A useful reply")).toBeTruthy();
    expect(within(document.querySelector(".conversation-log") as HTMLElement).queryByText("Retry this turn")).toBeNull();
    expect(screen.getByText(/stopped waiting.*back in the composer.*finish this request in the background/i)).toBeTruthy();

    apiMocks.sendChatTurn.mockResolvedValueOnce({ text: "Retried reply", elapsed_ms: 456, usage: {} });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText("Retried reply")).toBeTruthy();
    expect(apiMocks.sendChatTurn.mock.calls[2][2]).toEqual([
      { role: "user", content: "Earlier turn" },
      { role: "assistant", content: "A useful reply" },
      { role: "user", content: "Retry this turn" },
    ]);
  });

  it("blocks editing during transcription and aborts it when the session is cleared", async () => {
    class FakeRecorder {
      static isTypeSupported() { return true; }
      ondataavailable?: (event: { data: Blob }) => void;
      onstop?: () => void;
      mimeType = "audio/webm";
      start() {}
      stop() {
        this.ondataavailable?.({ data: new Blob(["audio"], { type: this.mimeType }) });
        this.onstop?.();
      }
    }
    const track = { stop: vi.fn() };
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [track] }) },
    });
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    let transcriptionSignal: AbortSignal | undefined;
    let resolveTranscription: ((result: { text: string }) => void) | undefined;
    apiMocks.transcribeAudio.mockImplementation((_csrf, _audio, signal) => new Promise((resolve) => {
      transcriptionSignal = signal;
      resolveTranscription = resolve;
    }));
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const clear = await screen.findByRole("button", { name: "Clear session" }) as HTMLButtonElement;
    const pushToTalk = screen.getByRole("button", { name: "Push to talk" });
    fireEvent.pointerDown(pushToTalk);
    await screen.findByRole("button", { name: "Release to transcribe" });
    fireEvent.pointerUp(pushToTalk);

    expect(await screen.findByRole("button", { name: "Transcribing speech" })).toBeTruthy();
    const composer = screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement;
    expect(composer.disabled).toBe(true);
    expect(clear.disabled).toBe(false);
    fireEvent.click(clear);

    expect(transcriptionSignal?.aborted).toBe(true);
    expect(composer.value).toBe("");
    await act(async () => {
      resolveTranscription?.({ text: "Late transcript" });
      await Promise.resolve();
    });
    expect(composer.value).toBe("");
    expect(screen.queryByText(/transcript ready/i)).toBeNull();
    expect(track.stop).toHaveBeenCalled();
  });

  it("keeps chat closed while stop is pending and refreshes status after a timeout", async () => {
    let rejectStop: ((error: Error) => void) | undefined;
    apiMocks.getExamples.mockResolvedValueOnce([makeStatus("ready")]).mockResolvedValueOnce([makeStatus("stopping")]);
    apiMocks.stopExample.mockImplementation(() => new Promise((_resolve, reject) => { rejectStop = reject; }));
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Stop example" }));

    expect(await screen.findByText("Stopping")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
    expect(apiMocks.sendChatTurn).not.toHaveBeenCalled();
    await act(async () => {
      rejectStop?.(new Error("browser request timed out"));
      await Promise.resolve();
    });

    expect(await screen.findByText("Stopping")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
    expect(apiMocks.getExamples).toHaveBeenCalledTimes(2);
  });

  it("can synthesize and play a reply through the dashboard API", async () => {
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:spoken-reply"),
      revokeObjectURL: vi.fn(),
    });
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByRole("button", { name: "Send message" });
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Say hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("A useful reply");
    fireEvent.click(screen.getByRole("button", { name: "Speak reply" }));

    expect(await screen.findByLabelText("Spoken reply audio")).toBeTruthy();
    expect(apiMocks.synthesizeReply).toHaveBeenCalledWith("csrf-value", "A useful reply", expect.any(AbortSignal));
  });

  it("sends CSRF with lifecycle requests and prevents duplicate starts", async () => {
    let resolveStart: ((status: ExamplePublicStatus) => void) | undefined;
    apiMocks.startExample.mockImplementation(() => new Promise((resolve) => { resolveStart = resolve; }));
    apiMocks.getExamples.mockResolvedValueOnce([makeStatus("stopped")]).mockResolvedValue([makeStatus("starting")]);
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const start = await screen.findByRole("button", { name: "Start example" });

    fireEvent.click(start);
    expect(start.hasAttribute("disabled")).toBe(true);
    expect(apiMocks.startExample).toHaveBeenCalledTimes(1);
    expect(apiMocks.startExample).toHaveBeenCalledWith("csrf-value");
    resolveStart?.(makeStatus("starting"));
    expect(await screen.findByText("Preparing")).toBeTruthy();
  });

  it("shows safe conflict and session-expiry feedback", async () => {
    const onSessionExpired = vi.fn();
    apiMocks.getExamples.mockResolvedValue([makeStatus("stopped")]);
    apiMocks.startExample.mockRejectedValueOnce({ status: 409 });
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={onSessionExpired} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start example" }));
    expect((await screen.findByRole("alert")).textContent?.toLowerCase()).toMatch(/another dashboard activity/i);

    apiMocks.getExamples.mockRejectedValueOnce({ status: 401 });
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    await waitFor(() => expect(onSessionExpired).toHaveBeenCalledTimes(1));
  });

  it("shows CSRF and network failures without displaying server details", async () => {
    apiMocks.getExamples.mockResolvedValue([makeStatus("stopped")]);
    apiMocks.startExample.mockRejectedValueOnce({ status: 403 }).mockRejectedValueOnce(new Error("Failed to fetch"));
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start example" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/request verification failed/i);
    fireEvent.click(screen.getByRole("button", { name: "Start example" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/request could not be completed/i);
  });

  it("prevents duplicate stop requests while a stop is pending", async () => {
    let resolveStop: ((status: ExamplePublicStatus) => void) | undefined;
    apiMocks.stopExample.mockImplementation(() => new Promise((resolve) => { resolveStop = resolve; }));
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const stop = await screen.findByRole("button", { name: "Stop example" });
    fireEvent.click(stop);
    expect(stop.hasAttribute("disabled")).toBe(true);
    expect(apiMocks.stopExample).toHaveBeenCalledTimes(1);
    expect(apiMocks.stopExample).toHaveBeenCalledWith("csrf-value");
    resolveStop?.(makeStatus("stopping"));
  });

  it("reports microphone denial without keeping a recording stream", async () => {
    class FakeRecorder {
      static isTypeSupported() { return true; }
    }
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    const getUserMedia = vi.fn().mockRejectedValue(new Error("Permission denied"));
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const pushToTalk = await screen.findByRole("button", { name: /push to talk/i });
    fireEvent.pointerDown(pushToTalk);

    expect((await screen.findByRole("alert")).textContent?.toLowerCase()).toMatch(/microphone access was denied/i);
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(apiMocks.transcribeAudio).not.toHaveBeenCalled();
  });

  it("keeps an empty transcript editable and never sends it automatically", async () => {
    class FakeRecorder {
      static isTypeSupported() { return true; }
      ondataavailable?: (event: { data: Blob }) => void;
      onstop?: () => void;
      mimeType = "audio/webm";
      start() {}
      stop() {
        this.ondataavailable?.({ data: new Blob(["audio"], { type: this.mimeType }) });
        this.onstop?.();
      }
    }
    const track = { stop: vi.fn() };
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [track] }) },
    });
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    apiMocks.transcribeAudio.mockResolvedValue({ text: "" });
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const pushToTalk = await screen.findByRole("button", { name: /push to talk/i });
    fireEvent.pointerDown(pushToTalk);
    await waitFor(() => expect(screen.getByRole("button", { name: /release to transcribe/i })).toBeTruthy());
    fireEvent.pointerUp(pushToTalk);

    expect(await screen.findByText(/no speech detected/i)).toBeTruthy();
    expect(apiMocks.sendChatTurn).not.toHaveBeenCalled();
    expect(track.stop).toHaveBeenCalled();
  });

  it("lets the user edit a transcript before choosing to send it", async () => {
    class FakeRecorder {
      static isTypeSupported() { return true; }
      ondataavailable?: (event: { data: Blob }) => void;
      onstop?: () => void;
      mimeType = "audio/webm";
      start() {}
      stop() {
        this.ondataavailable?.({ data: new Blob(["audio"], { type: this.mimeType }) });
        this.onstop?.();
      }
    }
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }) },
    });
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    apiMocks.transcribeAudio.mockResolvedValue({ text: "Godmorgen alle sammen" });
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const composer = await screen.findByRole("textbox", { name: "Message" }) as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: "Typed before speaking" } });
    const pushToTalk = await screen.findByRole("button", { name: /push to talk/i });
    fireEvent.pointerDown(pushToTalk);
    await waitFor(() => expect(screen.getByRole("button", { name: /release to transcribe/i })).toBeTruthy());
    fireEvent.pointerUp(pushToTalk);

    await waitFor(() => expect(composer.value).toBe("Typed before speaking\nGodmorgen alle sammen"));
    expect(apiMocks.sendChatTurn).not.toHaveBeenCalled();
    fireEvent.change(composer, { target: { value: "Typed before speaking\nGodmorgen, team" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(apiMocks.sendChatTurn).toHaveBeenCalled());
    expect(apiMocks.sendChatTurn.mock.calls[0][2]).toEqual([
      { role: "user", content: "Typed before speaking\nGodmorgen, team" },
    ]);
  });

  it("cancels recording on pointer cancellation and discards its audio", async () => {
    class FakeRecorder {
      static isTypeSupported() { return true; }
      ondataavailable?: (event: { data: Blob }) => void;
      onstop?: () => void;
      mimeType = "audio/webm";
      start() {}
      stop() {
        this.ondataavailable?.({ data: new Blob(["private audio"], { type: this.mimeType }) });
        this.onstop?.();
      }
    }
    const track = { stop: vi.fn() };
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [track] }) },
    });
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const pushToTalk = await screen.findByRole("button", { name: /push to talk/i });
    fireEvent.pointerDown(pushToTalk);
    await waitFor(() => expect(screen.getByRole("button", { name: /release to transcribe/i })).toBeTruthy());
    fireEvent.pointerCancel(pushToTalk);

    await waitFor(() => expect(track.stop).toHaveBeenCalled());
    expect(apiMocks.transcribeAudio).not.toHaveBeenCalled();
    expect(screen.getByText(/recording cancelled/i)).toBeTruthy();
  });

  it("automatically stops capture at the 120 second limit", async () => {
    class FakeRecorder {
      static isTypeSupported() { return true; }
      ondataavailable?: (event: { data: Blob }) => void;
      onstop?: () => void;
      mimeType = "audio/webm";
      start() {}
      stop() {
        this.ondataavailable?.({ data: new Blob(["bounded audio"], { type: this.mimeType }) });
        this.onstop?.();
      }
    }
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }) },
    });
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    render(<ExamplesPage csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const pushToTalk = await screen.findByRole("button", { name: /push to talk/i });
    vi.useFakeTimers();
    await act(async () => {
      fireEvent.pointerDown(pushToTalk);
      await Promise.resolve();
    });
    expect(pushToTalk.getAttribute("aria-label")).toBe("Release to transcribe");
    await act(async () => {
      vi.advanceTimersByTime(120_000);
      await Promise.resolve();
    });

    expect(apiMocks.transcribeAudio).toHaveBeenCalledTimes(1);
    expect(pushToTalk.getAttribute("aria-label")).toBe("Push to talk");
  });
});
