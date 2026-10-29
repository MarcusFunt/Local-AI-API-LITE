import { describe, expect, it, vi } from "vitest";

import { startDashboardPolling } from "./polling";

describe("dashboard polling", () => {
  it("keeps one request and one timer when visibility returns during an in-flight refresh", async () => {
    let visibility: DocumentVisibilityState = "visible";
    let listener: (() => void) | undefined;
    let nextTimerId = 1;
    const timers = new Map<number, { callback: () => void; delay: number }>();
    const schedule = vi.fn((callback: () => void, delay: number) => {
      const id = nextTimerId++;
      timers.set(id, { callback, delay });
      return id;
    });
    const cancel = vi.fn((id: number) => timers.delete(id));
    const subscribe = vi.fn((callback: () => void) => {
      listener = callback;
      return () => {
        listener = undefined;
      };
    });
    let resolveFirstRefresh!: () => void;
    let refreshNumber = 0;
    const refresh = vi.fn(() => {
      refreshNumber += 1;
      if (refreshNumber === 1) {
        return new Promise<void>((resolve) => {
          resolveFirstRefresh = resolve;
        });
      }
      return Promise.resolve();
    });

    const stop = startDashboardPolling({
      refresh,
      onError: vi.fn(),
      getVisibilityState: () => visibility,
      subscribeVisibility: subscribe,
      schedule,
      cancel,
      nextDelay: () => (visibility === "hidden" ? 30_000 : 5_000),
      hiddenDelay: 30_000,
    });

    expect(refresh).toHaveBeenCalledTimes(1);
    visibility = "hidden";
    listener?.();
    visibility = "visible";
    listener?.();
    expect(refresh).toHaveBeenCalledTimes(1);

    resolveFirstRefresh();
    await Promise.resolve();
    await Promise.resolve();
    expect(schedule).toHaveBeenCalledTimes(1);
    expect(timers.size).toBe(1);

    const [timerId, timer] = [...timers.entries()][0];
    timers.delete(timerId);
    timer.callback();
    await Promise.resolve();
    await Promise.resolve();
    expect(refresh).toHaveBeenCalledTimes(2);
    expect(timers.size).toBe(1);

    stop();
    expect(timers.size).toBe(0);
    expect(cancel).toHaveBeenCalledTimes(1);
  });
});
