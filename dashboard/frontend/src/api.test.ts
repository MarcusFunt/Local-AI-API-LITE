import { afterEach, describe, expect, it, vi } from "vitest";

import { sendChatTurn, stopExample, synthesizeReply, transcribeAudio } from "./api";

describe("stopExample", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("allows the bounded container stop to finish before its client timeout", async () => {
    let observedTimeoutMs = 0;
    vi.spyOn(AbortSignal, "timeout").mockImplementation((milliseconds) => {
      observedTimeoutMs = milliseconds;
      return new AbortController().signal;
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ state: "stopped" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));

    await stopExample("csrf-value");

    expect(observedTimeoutMs).toBe(30_000);
  });
});

describe("example language requests", () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each(["da", "en"] as const)("sends %s in the chat JSON body", async (language) => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ text: "Reply", elapsed_ms: 1 })));
    vi.stubGlobal("fetch", fetch);
    await sendChatTurn("csrf-value", language, "balanced", [{ role: "user", content: "Hello" }]);
    expect(fetch.mock.calls[0][0]).toBe("/api/control/v1/examples/chat-voice/chat");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ language, preset: "balanced", messages: [{ role: "user", content: "Hello" }] });
  });

  it.each(["da", "en"] as const)("sends %s beside the recorded file in multipart transcription", async (language) => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ text: "Transcript" })));
    vi.stubGlobal("fetch", fetch);
    await transcribeAudio("csrf-value", new Blob(["audio"], { type: "audio/wav" }), language);
    const request = fetch.mock.calls[0][1];
    expect(fetch.mock.calls[0][0]).toBe("/api/control/v1/examples/chat-voice/transcribe");
    expect(request.body.get("language")).toBe(language);
    expect([...request.body.keys()]).toEqual(["file", "language"]);
    expect(request.body.get("file").name).toBe("recording.wav");
    expect(request.headers["X-CSRF-Token"]).toBe("csrf-value");
  });

  it.each(["da", "en"] as const)("sends %s in the synthesis JSON body", async (language) => {
    const fetch = vi.fn().mockResolvedValue(new Response("RIFF audio", { headers: { "Content-Type": "audio/wav" } }));
    vi.stubGlobal("fetch", fetch);
    await synthesizeReply("csrf-value", "Speak this", language);
    expect(fetch.mock.calls[0][0]).toBe("/api/control/v1/examples/chat-voice/synthesize");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ text: "Speak this", language });
  });
});
