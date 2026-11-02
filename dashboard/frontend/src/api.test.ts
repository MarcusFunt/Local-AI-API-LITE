import { afterEach, describe, expect, it, vi } from "vitest";

import { stopExample } from "./api";

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
