export interface DashboardPollingOptions {
  refresh: () => Promise<unknown>;
  onError: (cause: unknown) => void;
  getVisibilityState: () => DocumentVisibilityState;
  subscribeVisibility: (listener: () => void) => () => void;
  schedule: (callback: () => void, delay: number) => number;
  cancel: (timerId: number) => void;
  nextDelay: () => number;
  hiddenDelay: number;
}

export function startDashboardPolling(options: DashboardPollingOptions): () => void {
  let stopped = false;
  let inFlight = false;
  let timer: number | undefined;

  const clearTimer = () => {
    if (timer !== undefined) {
      options.cancel(timer);
      timer = undefined;
    }
  };

  const scheduleNext = (delay: number) => {
    if (stopped) return;
    clearTimer();
    timer = options.schedule(() => {
      timer = undefined;
      void poll();
    }, delay);
  };

  const poll = async () => {
    if (stopped || inFlight) return;
    if (options.getVisibilityState() === "hidden") {
      scheduleNext(options.hiddenDelay);
      return;
    }

    inFlight = true;
    try {
      await options.refresh();
    } catch (cause) {
      if (!stopped) options.onError(cause);
    } finally {
      inFlight = false;
      if (!stopped) scheduleNext(options.nextDelay());
    }
  };

  const handleVisibility = () => {
    if (options.getVisibilityState() !== "visible") return;
    clearTimer();
    void poll();
  };

  const unsubscribe = options.subscribeVisibility(handleVisibility);
  void poll();

  return () => {
    stopped = true;
    clearTimer();
    unsubscribe();
  };
}
