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

function installRecorder() {
  class FakeRecorder {
    static isTypeSupported() { return true; }
    ondataavailable?: (event: { data: Blob }) => void;
    onstop?: () => void;
    mimeType = "audio/webm";
    state = "inactive";
    start() { this.state = "recording"; }
    stop() {
      this.state = "inactive";
      this.ondataavailable?.({ data: new Blob(["audio"], { type: this.mimeType }) });
      this.onstop?.();
    }
  }
  const stream = { getTracks: () => [{ stop: vi.fn() }] };
  const getUserMedia = vi.fn().mockResolvedValue(stream);
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  return { getUserMedia, stream };
}

function installAudioUrls() {
  vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:spoken-reply"), revokeObjectURL: vi.fn() });
}

describe("ExamplesPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    ready();
  });

  it.each([
    "Explain how a local AI model works.",
    "Help me plan a small coding project.",
    "Give me three ideas to try in this chat.",
  ])("fills the composer with '%s' without sending", async (prompt) => {
    render(<ExamplesPage workspace onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const composer = await screen.findByRole("textbox", { name: "Message" }) as HTMLTextAreaElement;
    fireEvent.click(screen.getByRole("button", { name: prompt }));
    expect(composer.value).toBe(prompt);
    expect(apiMocks.sendChatTurn).not.toHaveBeenCalled();
    expect(screen.queryByRole("article", { name: "Your message" })).toBeNull();
    expect((screen.getByRole("button", { name: "Send message" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("names each message by its speaker and removes starters after sending", async () => {
    render(<ExamplesPage workspace onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const composer = await screen.findByRole("textbox", { name: "Message" });
    fireEvent.change(composer, { target: { value: "My local question" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByRole("article", { name: "Assistant reply" })).toBeTruthy();
    expect(within(screen.getByRole("article", { name: "Your message" })).getByText("My local question")).toBeTruthy();
    expect(within(screen.getByRole("article", { name: "Assistant reply" })).getByText("A useful reply")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Explain how a local AI model works." })).toBeNull();
  });

  it("keeps a long reply intact with its timing and working speech action", async () => {
    installAudioUrls();
    const longReply = `First paragraph.\n\n${"A detailed local answer. ".repeat(100)}\n${"x".repeat(300)}\nFinal sentence.`;
    apiMocks.sendChatTurn.mockResolvedValue({ text: longReply, elapsed_ms: 876, usage: {} });
    render(<ExamplesPage workspace onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    fireEvent.change(await screen.findByRole("textbox", { name: "Message" }), { target: { value: "Explain in detail" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    const reply = within(await screen.findByRole("article", { name: "Assistant reply" }));
    expect(reply.getByText((_, element) => element?.tagName === "P" && element.textContent === longReply)).toBeTruthy();
    expect(reply.getByText("876 ms")).toBeTruthy();
    const speak = reply.getByRole("button", { name: "Speak reply" }) as HTMLButtonElement;
    expect(speak.disabled).toBe(false);
    fireEvent.click(speak);
    expect(await screen.findByLabelText("Spoken reply audio")).toBeTruthy();
    expect(apiMocks.synthesizeReply).toHaveBeenCalledWith("csrf-value", longReply, "da", expect.any(AbortSignal));
  });

  it("announces a timeout-style failure and restores the prompt for retry", async () => {
    apiMocks.sendChatTurn.mockRejectedValue(new Error("Request timed out"));
    render(<ExamplesPage workspace onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const composer = await screen.findByRole("textbox", { name: "Message" }) as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: "A request that times out" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Your message is back in the composer so you can retry.");
    expect(composer.value).toBe("A request that times out");
    expect(screen.queryByRole("timer")).toBeNull();
  });

  it.each([
    { state: "ready", label: "Ready", start: "Start example", startDisabled: true, stop: "Stop example", stopDisabled: false },
    { state: "starting", label: "Preparing", start: "Start example", startDisabled: true, stop: "Stop example", stopDisabled: false },
    { state: "stopping", label: "Stopping", start: "Start example", startDisabled: true, stop: "Stopping…", stopDisabled: true },
    { state: "failed", label: "Failed", start: "Retry start", startDisabled: false, stop: "Stop example", stopDisabled: false },
    { state: "recovery-required", label: "Recovery required", start: "Start example", startDisabled: true, stop: "Stop example", stopDisabled: false },
  ] as const)("exposes $state status and lifecycle actions in the workspace header", async ({ state, label, start, startDisabled, stop, stopDisabled }) => {
    apiMocks.getExamples.mockResolvedValue([makeStatus(state)]);
    render(<ExamplesPage workspace onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const workspace = within(screen.getByRole("region", { name: "Chat + Voice workspace" }));
    expect(await workspace.findByText(label, { exact: true })).toBeTruthy();
    expect(workspace.getByRole("heading", { level: 1, name: "Chat + Voice" })).toBeTruthy();
    expect((workspace.getByRole("button", { name: start }) as HTMLButtonElement).disabled).toBe(startDisabled);
    expect((workspace.getByRole("button", { name: stop }) as HTMLButtonElement).disabled).toBe(stopDisabled);
    expect((workspace.getByRole("button", { name: "Refresh status" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("politely announces workspace lifecycle changes", async () => {
    apiMocks.getExamples
      .mockResolvedValueOnce([makeStatus("starting")])
      .mockResolvedValueOnce([makeStatus("ready")]);
    render(<ExamplesPage workspace onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const workspace = within(screen.getByRole("region", { name: "Chat + Voice workspace" }));
    await workspace.findByText("Preparing", { exact: true });
    const lifecycleStatus = workspace.getByRole("status");

    expect(lifecycleStatus.textContent).toContain("Preparing");
    expect(lifecycleStatus.getAttribute("aria-live")).toBe("polite");
    fireEvent.click(workspace.getByRole("button", { name: "Refresh status" }));
    await waitFor(() => expect(lifecycleStatus.textContent).toContain("Ready"));
    expect(await workspace.findByRole("status")).toBe(lifecycleStatus);
  });

  it("keeps text chat usable and explains stopped voice dependencies in the workspace", async () => {
    apiMocks.getExamples.mockResolvedValue([makeStatus("ready", { voice: "stopped" })]);
    render(<ExamplesPage workspace onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const message = await screen.findByRole("textbox", { name: "Message" });
    expect((message as HTMLTextAreaElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "Push to talk" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Chat is ready while push-to-talk and spoken replies wait for both ddu-stt and ddu-tts/)).toBeTruthy();
    fireEvent.change(message, { target: { value: "Text still works" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText("A useful reply")).toBeTruthy();
  });

  it("discloses workspace dependencies and recovery guidance only when service details are expanded", async () => {
    apiMocks.getExamples.mockResolvedValue([{ ...makeStatus("recovery-required"), last_error_category: "recovery_required" }]);
    render(<ExamplesPage workspace onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByText("Recovery required");
    const summary = screen.getByText("Services and status details", { selector: "summary" });
    const disclosure = summary.closest("details")!;
    expect(disclosure.open).toBe(false);
    fireEvent.click(summary);
    expect(disclosure.open).toBe(true);
    expect(within(disclosure).getByText("Chat model")).toBeTruthy();
    expect(within(disclosure).getByText("Speech services")).toBeTruthy();
    expect(within(disclosure).getByRole("status").textContent).toMatch(/confirm the container state before another start/);
    fireEvent.click(summary);
    expect(disclosure.open).toBe(false);
  });

  it("keeps workspace controls keyboard reachable and labels live voice feedback", async () => {
    installRecorder();
    render(<ExamplesPage workspace onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByRole("textbox", { name: "Message" });

    const focusableControls = [
      screen.getByText("Services and status details", { selector: "summary" }),
      screen.getByRole("combobox", { name: "Language" }),
      screen.getByRole("radio", { name: "Balanced" }),
      screen.getByRole("textbox", { name: "Message" }),
      screen.getByRole("button", { name: "Stop example" }),
      screen.getByRole("button", { name: "Push to talk" }),
    ];
    for (const control of focusableControls) {
      expect((control as HTMLElement).tabIndex).not.toBe(-1);
    }

    const pushToTalk = focusableControls.at(-1) as HTMLButtonElement;
    pushToTalk.focus();
    expect(document.activeElement).toBe(pushToTalk);
    fireEvent.keyDown(pushToTalk, { key: "Enter" });

    await screen.findByRole("button", { name: "Release to transcribe" });
    const voiceStatus = screen.getByRole("status", { name: "Voice input status" });
    expect(voiceStatus.getAttribute("aria-live")).toBe("polite");
    expect(voiceStatus.textContent).toBe("Recording");
    expect(document.querySelector(".recording-dot")?.getAttribute("aria-hidden")).toBe("true");
    expect(screen.getByRole("timer", { name: "Recording elapsed time 00:00" }).getAttribute("aria-live")).toBe("off");
    fireEvent.keyUp(pushToTalk, { key: "Enter" });
    expect(apiMocks.transcribeAudio).toHaveBeenCalledTimes(1);
  });

  it("waits for a successful catalog start to become ready before opening the workspace", async () => {
    const onOpenWorkspace = vi.fn();
    apiMocks.getExamples.mockResolvedValue([makeStatus("stopped")]);
    apiMocks.startExample.mockResolvedValue(makeStatus("starting"));
    render(<ExamplesPage workspace={false} onOpenWorkspace={onOpenWorkspace} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByText("Stopped");
    apiMocks.getExamples.mockResolvedValue([makeStatus("starting")]);
    fireEvent.click(screen.getByRole("button", { name: "Start example" }));
    await screen.findByText("Preparing");
    expect(onOpenWorkspace).not.toHaveBeenCalled();
    apiMocks.getExamples.mockResolvedValue([makeStatus("ready")]);
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    await waitFor(() => expect(onOpenWorkspace).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    await screen.findByText("Ready");
    expect(onOpenWorkspace).toHaveBeenCalledTimes(1);
  });

  it("opens the workspace when ready despite an older stopped refresh resolving after a successful start", async () => {
    const onOpenWorkspace = vi.fn();
    apiMocks.getExamples.mockResolvedValue([makeStatus("stopped")]);
    apiMocks.startExample.mockResolvedValue(makeStatus("starting"));
    render(<ExamplesPage workspace={false} onOpenWorkspace={onOpenWorkspace} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByText("Stopped");
    let resolveOldRefresh!: (statuses: ExamplePublicStatus[]) => void;
    apiMocks.getExamples.mockImplementationOnce(() => new Promise((resolve) => { resolveOldRefresh = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    apiMocks.getExamples.mockResolvedValue([makeStatus("starting")]);
    fireEvent.click(screen.getByRole("button", { name: "Start example" }));
    await screen.findByText("Preparing");
    await act(async () => resolveOldRefresh([makeStatus("stopped")]));
    apiMocks.getExamples.mockResolvedValue([makeStatus("ready")]);
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    await screen.findByText("Ready");
    expect(onOpenWorkspace).toHaveBeenCalledTimes(1);
  });

  it("keeps the newer lifecycle observation when overlapping refreshes complete out of order", async () => {
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByText("Ready");
    let resolveOldRefresh!: (statuses: ExamplePublicStatus[]) => void;
    apiMocks.getExamples.mockImplementationOnce(() => new Promise((resolve) => { resolveOldRefresh = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    apiMocks.getExamples.mockResolvedValue([makeStatus("starting")]);
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    await screen.findByText("Preparing");
    await act(async () => resolveOldRefresh([makeStatus("stopped")]));
    expect(screen.queryByText("Stopped")).toBeNull();
    expect(screen.getByText("Preparing")).toBeTruthy();
  });

  it("ignores a refresh from before Stop while the lifecycle operation is pending", async () => {
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByText("Ready");
    let resolveOldRefresh!: (statuses: ExamplePublicStatus[]) => void;
    let resolveStop!: (status: ExamplePublicStatus) => void;
    apiMocks.getExamples.mockImplementationOnce(() => new Promise((resolve) => { resolveOldRefresh = resolve; }));
    apiMocks.stopExample.mockImplementationOnce(() => new Promise((resolve) => { resolveStop = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    fireEvent.click(screen.getByRole("button", { name: "Stop example" }));
    await screen.findByText("Stopping");
    await act(async () => resolveOldRefresh([makeStatus("ready")]));
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.getByText("Stopping")).toBeTruthy();
    apiMocks.getExamples.mockResolvedValue([makeStatus("stopped")]);
    await act(async () => resolveStop(makeStatus("stopped")));
    expect(await screen.findByText("Stopped")).toBeTruthy();
  });

  it("stays in the catalog with a start error after a rejected start, even if status later becomes ready", async () => {
    const onOpenWorkspace = vi.fn();
    apiMocks.getExamples.mockResolvedValue([makeStatus("stopped")]);
    apiMocks.startExample.mockRejectedValue(new Error("start rejected"));
    render(<ExamplesPage workspace={false} onOpenWorkspace={onOpenWorkspace} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByText("Stopped");
    fireEvent.click(screen.getByRole("button", { name: "Start example" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "The example start request could not be completed. Refresh status and try again.");
    apiMocks.getExamples.mockResolvedValue([makeStatus("ready")]);
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    await screen.findByText("Ready");
    expect(onOpenWorkspace).not.toHaveBeenCalled();
  });

  it("defaults language to Danish, preserves the conversation when changed, and resets after reload", async () => {
    const page = render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const language = await screen.findByRole("combobox", { name: "Language" }) as HTMLSelectElement;
    expect(language.id).toBe("example-language");
    expect(language.value).toBe("da");
    expect(Array.from(language.options, (option) => [option.value, option.text])).toEqual([["da", "Danish"], ["en", "English"]]);
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "First turn" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("A useful reply");
    fireEvent.change(language, { target: { value: "en" } });
    expect(screen.getByText("First turn")).toBeTruthy();
    expect(screen.getByText("A useful reply")).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Next turn" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(apiMocks.sendChatTurn).toHaveBeenCalledTimes(2));
    expect(apiMocks.sendChatTurn.mock.calls[0][1]).toBe("da");
    expect(apiMocks.sendChatTurn.mock.calls[1].slice(1, 4)).toEqual(["en", "balanced", [
      { role: "user", content: "First turn" }, { role: "assistant", content: "A useful reply" }, { role: "user", content: "Next turn" },
    ]]);
    page.unmount();
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    expect((await screen.findByRole("combobox", { name: "Language" }) as HTMLSelectElement).value).toBe("da");
    expect(screen.queryByText("First turn")).toBeNull();
  });

  it("sends English selection to chat, transcription, and manual speech", async () => {
    installRecorder();
    installAudioUrls();
    apiMocks.transcribeAudio.mockResolvedValue({ text: "Good morning" });
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    fireEvent.change(await screen.findByRole("combobox", { name: "Language" }), { target: { value: "en" } });
    expect(screen.getByText("Turn-based English voice input")).toBeTruthy();
    const push = screen.getByRole("button", { name: "Push to talk" });
    fireEvent.pointerDown(push);
    await screen.findByRole("button", { name: "Release to transcribe" });
    fireEvent.pointerUp(push);
    await waitFor(() => expect((screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement).value).toBe("Good morning"));
    expect(apiMocks.transcribeAudio).toHaveBeenCalledWith("csrf-value", expect.any(Blob), "en", expect.any(AbortSignal));
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("A useful reply");
    expect(apiMocks.sendChatTurn).toHaveBeenCalledWith("csrf-value", "en", "balanced", [{ role: "user", content: "Good morning" }], expect.any(AbortSignal));
    fireEvent.click(screen.getByRole("button", { name: "Speak reply" }));
    await screen.findByLabelText("Spoken reply audio");
    expect(apiMocks.synthesizeReply).toHaveBeenCalledWith("csrf-value", "A useful reply", "en", expect.any(AbortSignal));
  });

  it("keeps the chat language captured at start for automatic speech while later actions use the new selection", async () => {
    installAudioUrls();
    let resolveChat: ((reply: { text: string; elapsed_ms: number }) => void) | undefined;
    apiMocks.sendChatTurn.mockImplementationOnce(() => new Promise((resolve) => { resolveChat = resolve; }));
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const language = await screen.findByRole("combobox", { name: "Language" });
    fireEvent.click(screen.getByRole("checkbox", { name: "Speak replies automatically" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Danish turn" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    fireEvent.change(language, { target: { value: "en" } });
    await act(async () => { resolveChat?.({ text: "Danish reply", elapsed_ms: 1 }); });
    await screen.findByLabelText("Spoken reply audio");
    expect(apiMocks.sendChatTurn.mock.calls[0][1]).toBe("da");
    expect(apiMocks.synthesizeReply.mock.calls[0].slice(0, 3)).toEqual(["csrf-value", "Danish reply", "da"]);
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "English turn" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("A useful reply");
    await waitFor(() => expect(apiMocks.synthesizeReply).toHaveBeenCalledTimes(2));
    expect(apiMocks.sendChatTurn.mock.calls[1][1]).toBe("en");
    expect(apiMocks.synthesizeReply.mock.calls[1].slice(0, 3)).toEqual(["csrf-value", "A useful reply", "en"]);
    fireEvent.change(language, { target: { value: "da" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Speak reply" })[1]);
    await waitFor(() => expect(apiMocks.synthesizeReply).toHaveBeenCalledTimes(3));
    expect(apiMocks.synthesizeReply.mock.calls[2].slice(0, 3)).toEqual(["csrf-value", "A useful reply", "da"]);
  });

  it("captures recording language before awaiting microphone permission and uses the new selection for the next recording", async () => {
    const { getUserMedia, stream } = installRecorder();
    let allowMicrophone: ((stream: unknown) => void) | undefined;
    getUserMedia.mockImplementationOnce(() => new Promise((resolve) => { allowMicrophone = resolve; }));
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const language = await screen.findByRole("combobox", { name: "Language" });
    const push = screen.getByRole("button", { name: "Push to talk" });
    fireEvent.pointerDown(push);
    fireEvent.change(language, { target: { value: "en" } });
    await act(async () => { allowMicrophone?.(stream); });
    await screen.findByRole("button", { name: "Release to transcribe" });
    fireEvent.pointerUp(push);
    await waitFor(() => expect(apiMocks.transcribeAudio).toHaveBeenCalledTimes(1));
    expect(apiMocks.transcribeAudio.mock.calls[0][2]).toBe("da");
    await screen.findByRole("button", { name: "Push to talk" });
    fireEvent.pointerDown(push);
    await screen.findByRole("button", { name: "Release to transcribe" });
    fireEvent.pointerUp(push);
    await waitFor(() => expect(apiMocks.transcribeAudio).toHaveBeenCalledTimes(2));
    expect(apiMocks.transcribeAudio.mock.calls[1][2]).toBe("en");
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
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    expect(await screen.findByText(label)).toBeTruthy();
  });

  it("requires the narrator to start and shows voice as an optional paired dependency", async () => {
    apiMocks.getExamples.mockResolvedValue([makeStatus("stopped", { chat: "stopped", voice: "stopped" })]);
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);

    expect(await screen.findByText(/ddu-narrator is not ready/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start example" }).hasAttribute("disabled")).toBe(true);

    apiMocks.getExamples.mockResolvedValue([makeStatus("ready", { voice: "stopped" })]);
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    expect(await screen.findByText(/voice is unavailable/i)).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Message" }).hasAttribute("disabled")).toBe(false);
    expect(screen.getByRole("button", { name: /push to talk/i }).hasAttribute("disabled")).toBe(true);
  });

  it("uses the selected fixed preset for a multi-turn chat and displays timing", async () => {
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByRole("button", { name: "Send message" });
    fireEvent.click(screen.getByRole("radio", { name: "Quality" }));
    expect((screen.getByRole("radio", { name: "Quality" }) as HTMLInputElement).checked).toBe(true);
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Compare two ideas" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByText("A useful reply")).toBeTruthy();
    expect(screen.getByText("321 ms")).toBeTruthy();
    await waitFor(() => expect(apiMocks.sendChatTurn).toHaveBeenCalled());
    expect(apiMocks.sendChatTurn.mock.calls[0].slice(0, 4)).toEqual([
      "csrf-value",
      "da",
      "quality",
      [{ role: "user", content: "Compare two ideas" }],
    ]);
    expect(screen.getByText(/does not guarantee correctness/i)).toBeTruthy();
  });

  it.each([
    ["da", "Fast", "fast"],
    ["da", "Balanced", "balanced"],
    ["da", "Quality", "quality"],
    ["en", "Fast", "fast"],
    ["en", "Balanced", "balanced"],
    ["en", "Quality", "quality"],
  ])("keeps %s language and %s depth through an edited voice turn", async (language, label, preset) => {
    installRecorder();
    installAudioUrls();
    apiMocks.transcribeAudio.mockResolvedValue({ text: "Draft transcript" });
    render(<ExamplesPage workspace onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    fireEvent.change(await screen.findByRole("combobox", { name: "Language" }), { target: { value: language } });
    const depth = screen.getByRole("radiogroup", { name: "Response depth" });
    expect(within(depth).getAllByRole("radio")).toHaveLength(3);
    expect((within(depth).getByRole("radio", { name: "Balanced" }) as HTMLInputElement).checked).toBe(true);
    fireEvent.click(within(depth).getByRole("radio", { name: label }));
    expect((within(depth).getByRole("radio", { name: label }) as HTMLInputElement).checked).toBe(true);
    const dock = screen.getByRole("region", { name: "Message and voice composer" });
    fireEvent.click(within(dock).getByRole("checkbox", { name: "Speak replies automatically" }));
    const push = within(dock).getByRole("button", { name: "Push to talk" });
    fireEvent.pointerDown(push);
    await screen.findByRole("button", { name: "Release to transcribe" });
    fireEvent.pointerUp(push);
    const composer = within(dock).getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement;
    await waitFor(() => expect(composer.value).toBe("Draft transcript"));
    expect(composer.disabled).toBe(false);
    expect(apiMocks.transcribeAudio).toHaveBeenCalledWith("csrf-value", expect.any(Blob), language, expect.any(AbortSignal));
    expect(apiMocks.sendChatTurn).not.toHaveBeenCalled();
    fireEvent.change(composer, { target: { value: "Reviewed transcript" } });
    fireEvent.click(within(dock).getByRole("button", { name: "Send message" }));
    await screen.findByLabelText("Spoken reply audio");
    expect(apiMocks.sendChatTurn).toHaveBeenCalledWith("csrf-value", language, preset, [{ role: "user", content: "Reviewed transcript" }], expect.any(AbortSignal));
    expect(apiMocks.synthesizeReply).toHaveBeenCalledWith("csrf-value", "A useful reply", language, expect.any(AbortSignal));
  });

  it("times only active capture and announces transcription politely after release", async () => {
    const { getUserMedia, stream } = installRecorder();
    let allowMicrophone!: (stream: unknown) => void;
    let finishTranscription!: (transcript: { text: string }) => void;
    getUserMedia.mockImplementationOnce(() => new Promise((resolve) => { allowMicrophone = resolve; }));
    apiMocks.transcribeAudio.mockImplementationOnce(() => new Promise((resolve) => { finishTranscription = resolve; }));
    render(<ExamplesPage workspace onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const push = await screen.findByRole("button", { name: "Push to talk" });
    vi.useFakeTimers();
    fireEvent.pointerDown(push);
    await act(async () => { vi.advanceTimersByTime(2000); });
    expect(screen.queryByRole("timer")).toBeNull();
    await act(async () => { allowMicrophone(stream); });
    const status = screen.getByRole("status", { name: "Voice input status" });
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(status.textContent).toMatch(/recording/i);
    expect(screen.getByRole("timer", { name: "Recording elapsed time 00:00" })).toBeTruthy();
    await act(async () => { vi.advanceTimersByTime(3000); });
    expect(screen.getByRole("timer", { name: "Recording elapsed time 00:03" }).getAttribute("aria-live")).toBe("off");
    fireEvent.pointerUp(push);
    expect(screen.queryByRole("timer")).toBeNull();
    expect(status.textContent).toMatch(/transcribing/i);
    await act(async () => { finishTranscription({ text: "Ready for review" }); });
    expect(status.textContent).toMatch(/ready to record/i);
    await act(async () => { vi.advanceTimersByTime(3000); });
    expect(screen.queryByRole("timer")).toBeNull();
    await act(async () => { fireEvent.pointerDown(push); });
    expect(screen.getByRole("timer", { name: "Recording elapsed time 00:00" })).toBeTruthy();
    fireEvent.pointerCancel(push);
    expect(screen.queryByRole("timer")).toBeNull();
    expect(status.textContent).toMatch(/ready to record/i);
    expect(apiMocks.transcribeAudio).toHaveBeenCalledTimes(1);
  });

  it.each([" ", "Enter"])("records while holding %j and ends capture on key release", async (key) => {
    installRecorder();
    render(<ExamplesPage workspace onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const push = await screen.findByRole("button", { name: "Push to talk" });
    fireEvent.keyDown(push, { key });
    await screen.findByRole("button", { name: "Release to transcribe" });
    expect(screen.getByRole("timer", { name: "Recording elapsed time 00:00" })).toBeTruthy();
    fireEvent.keyUp(push, { key });
    await screen.findByRole("button", { name: "Push to talk" });
    expect(screen.queryByRole("timer")).toBeNull();
    expect(apiMocks.transcribeAudio).toHaveBeenCalledTimes(1);
  });

  it.each(["Escape", "blur", "pointercancel"])("returns recording to idle and discards audio on %s", async (action) => {
    installRecorder();
    render(<ExamplesPage workspace onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const push = await screen.findByRole("button", { name: "Push to talk" });
    fireEvent.pointerDown(push);
    await screen.findByRole("button", { name: "Release to transcribe" });
    expect(screen.getByRole("timer", { name: "Recording elapsed time 00:00" })).toBeTruthy();
    if (action === "Escape") fireEvent.keyDown(window, { key: "Escape" });
    else if (action === "blur") fireEvent.blur(push);
    else fireEvent.pointerCancel(window);
    expect(screen.queryByRole("timer")).toBeNull();
    expect(screen.getByRole("status", { name: "Voice input status" }).textContent).toMatch(/ready to record/i);
    expect(apiMocks.transcribeAudio).not.toHaveBeenCalled();
  });

  it("restores a failed prompt to the composer so retry does not include an unanswered turn", async () => {
    apiMocks.sendChatTurn
      .mockRejectedValueOnce(new Error("temporary network failure"))
      .mockResolvedValueOnce({ text: "Recovered reply", elapsed_ms: 123, usage: {} });
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByRole("button", { name: "Send message" });

    const composer = screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement;
    fireEvent.change(composer, { target: { value: "Please keep this prompt" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(await screen.findByRole("alert")).toBeTruthy();
    await waitFor(() => expect(composer.value).toBe("Please keep this prompt"));
    expect(within(document.querySelector(".conversation-log") as HTMLElement).queryByText("Please keep this prompt")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByText("Recovered reply")).toBeTruthy();
    expect(apiMocks.sendChatTurn.mock.calls[1][3]).toEqual([
      { role: "user", content: "Please keep this prompt" },
    ]);
  });

  it("shows live elapsed time and changes to still working after ten seconds", async () => {
    let resolveChat: ((result: { text: string; elapsed_ms: number; usage: Record<string, number> }) => void) | undefined;
    apiMocks.sendChatTurn.mockImplementation(() => new Promise((resolve) => { resolveChat = resolve; }));
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByRole("button", { name: "Send message" });
    vi.useFakeTimers();

    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "A slow request" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    expect(screen.getByText("Waiting for a complete reply from the local model.").getAttribute("role")).toBe("status");
    expect(screen.getByRole("status", { name: "Voice input status" }).textContent).toBe("Voice controls paused during chat");
    expect(document.querySelector(".chat-request-spinner")?.getAttribute("aria-hidden")).toBe("true");
    expect(screen.getByRole("timer", { name: "Elapsed time 00:00" })).toBeTruthy();
    await act(async () => { vi.advanceTimersByTime(10_000); });
    expect(screen.getByText("Still working on your reply.").getAttribute("role")).toBe("status");
    expect(screen.getByRole("timer", { name: "Elapsed time 00:10" })).toBeTruthy();

    await act(async () => {
      resolveChat?.({ text: "Finished", elapsed_ms: 10_000, usage: {} });
      await Promise.resolve();
    });
    expect(screen.getByText("Finished")).toBeTruthy();
    expect(screen.queryByRole("timer", { name: "Elapsed time 00:10" })).toBeNull();
  });

  it("cancels a chat request and retries without duplicating its pending turn", async () => {
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByRole("button", { name: "Send message" });
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Earlier turn" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("A useful reply");

    let requestSignal: AbortSignal | undefined;
    apiMocks.sendChatTurn.mockImplementation((_csrf, _language, _preset, _messages, signal) => new Promise((_resolve, reject) => {
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
    expect(apiMocks.sendChatTurn.mock.calls[2][3]).toEqual([
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
    apiMocks.transcribeAudio.mockImplementation((_csrf, _audio, _language, signal) => new Promise((resolve) => {
      transcriptionSignal = signal;
      resolveTranscription = resolve;
    }));
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
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
    let resolveInitialStatus: ((statuses: ExamplePublicStatus[]) => void) | undefined;
    let rejectStop: ((error: Error) => void) | undefined;
    apiMocks.getExamples.mockImplementationOnce(() => new Promise((resolve) => { resolveInitialStatus = resolve; }))
      .mockResolvedValueOnce([makeStatus("stopping")]);
    apiMocks.stopExample.mockImplementation(() => new Promise((_resolve, reject) => { rejectStop = reject; }));
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const stopButton = await screen.findByRole("button", { name: "Stop example" });
    expect((stopButton as HTMLButtonElement).disabled).toBe(true);
    await act(async () => resolveInitialStatus?.([makeStatus("ready")]));
    await waitFor(() => expect((stopButton as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(stopButton);

    expect(await screen.findByRole("button", { name: /Stopping/ })).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
    expect(apiMocks.sendChatTurn).not.toHaveBeenCalled();
    await act(async () => {
      rejectStop?.(new Error("browser request timed out"));
    });

    await waitFor(() => expect(apiMocks.getExamples).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: /Stopping/ })).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
  });

  it("can synthesize and play a reply through the dashboard API", async () => {
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:spoken-reply"),
      revokeObjectURL: vi.fn(),
    });
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    await screen.findByRole("button", { name: "Send message" });
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: "Say hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("A useful reply");
    fireEvent.click(screen.getByRole("button", { name: "Speak reply" }));

    expect(await screen.findByLabelText("Spoken reply audio")).toBeTruthy();
    expect(apiMocks.synthesizeReply).toHaveBeenCalledWith("csrf-value", "A useful reply", "da", expect.any(AbortSignal));
  });

  it("sends CSRF with lifecycle requests and prevents duplicate starts", async () => {
    let resolveStart: ((status: ExamplePublicStatus) => void) | undefined;
    apiMocks.startExample.mockImplementation(() => new Promise((resolve) => { resolveStart = resolve; }));
    apiMocks.getExamples.mockResolvedValueOnce([makeStatus("stopped")]).mockResolvedValue([makeStatus("starting")]);
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
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
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={onSessionExpired} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start example" }));
    expect((await screen.findByRole("alert")).textContent?.toLowerCase()).toMatch(/another dashboard activity/i);

    apiMocks.getExamples.mockRejectedValueOnce({ status: 401 });
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    await waitFor(() => expect(onSessionExpired).toHaveBeenCalledTimes(1));
  });

  it("shows CSRF and network failures without displaying server details", async () => {
    apiMocks.getExamples.mockResolvedValue([makeStatus("stopped")]);
    apiMocks.startExample.mockRejectedValueOnce({ status: 403 }).mockRejectedValueOnce(new Error("Failed to fetch"));
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "Start example" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/request verification failed/i);
    fireEvent.click(screen.getByRole("button", { name: "Start example" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/request could not be completed/i);
  });

  it("prevents duplicate stop requests while a stop is pending", async () => {
    let resolveStop: ((status: ExamplePublicStatus) => void) | undefined;
    apiMocks.stopExample.mockImplementation(() => new Promise((resolve) => { resolveStop = resolve; }));
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const stop = await screen.findByRole("button", { name: "Stop example" });
    fireEvent.click(stop);
    expect(stop.hasAttribute("disabled")).toBe(true);
    expect(apiMocks.stopExample).toHaveBeenCalledTimes(1);
    expect(apiMocks.stopExample).toHaveBeenCalledWith("csrf-value");
    resolveStop?.(makeStatus("stopping"));
  });

  it("explains that phone microphone recording needs the Tailscale HTTPS page", async () => {
    class FakeRecorder {
      static isTypeSupported() { return true; }
    }
    vi.stubGlobal("isSecureContext", false);
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    const getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [] });
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    fireEvent.pointerDown(await screen.findByRole("button", { name: /push to talk/i }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/HTTPS/i);
    expect(alert.textContent).toMatch(/Tailscale/i);
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(apiMocks.transcribeAudio).not.toHaveBeenCalled();
  });

  it("reports microphone denial with browser settings guidance and no transcription", async () => {
    class FakeRecorder {
      static isTypeSupported() { return true; }
    }
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    const getUserMedia = vi.fn().mockRejectedValue(new Error("Permission denied"));
    Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const pushToTalk = await screen.findByRole("button", { name: /push to talk/i });
    fireEvent.pointerDown(pushToTalk);

    expect((await screen.findByRole("alert")).textContent?.toLowerCase()).toMatch(/allow microphone access.*browser settings/i);
    expect(screen.getByRole("status", { name: "Voice input status" }).textContent).toBe("Voice needs attention");
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
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
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
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
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
    expect(apiMocks.sendChatTurn.mock.calls[0][3]).toEqual([
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
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const pushToTalk = await screen.findByRole("button", { name: /push to talk/i });
    fireEvent.pointerDown(pushToTalk);
    await waitFor(() => expect(screen.getByRole("button", { name: /release to transcribe/i })).toBeTruthy());
    fireEvent.pointerCancel(pushToTalk);

    await waitFor(() => expect(track.stop).toHaveBeenCalled());
    expect(apiMocks.transcribeAudio).not.toHaveBeenCalled();
    expect(screen.getByText(/recording cancelled/i)).toBeTruthy();
  });

  it("stops recording when a phone pointer is released outside the hold button", async () => {
    class FakeRecorder {
      static isTypeSupported() { return true; }
      ondataavailable?: (event: { data: Blob }) => void;
      onstop?: () => void;
      mimeType = "audio/webm";
      state = "inactive";
      start() { this.state = "recording"; }
      stop() {
        this.state = "inactive";
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
    apiMocks.transcribeAudio.mockResolvedValue({ text: "Released outside" });
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
    const pushToTalk = await screen.findByRole("button", { name: /push to talk/i });
    fireEvent.pointerDown(pushToTalk);
    await screen.findByRole("button", { name: "Release to transcribe" });
    await act(async () => { await Promise.resolve(); });
    fireEvent.pointerUp(window);

    await waitFor(() => expect(apiMocks.transcribeAudio).toHaveBeenCalledTimes(1));
    expect(track.stop).toHaveBeenCalledTimes(1);
    expect((screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement).value).toBe("Released outside");
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
    render(<ExamplesPage workspace={false} onOpenWorkspace={vi.fn()} onReturnToCatalog={vi.fn()} csrfToken="csrf-value" onSessionExpired={vi.fn()} />);
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
