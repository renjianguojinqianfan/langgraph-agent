export interface PollTimers {
  setTimeout: (fn: () => void, ms: number) => number;
  clearTimeout: (handle: number) => void;
}

export interface FilePollLoopOptions<T> {
  intervalMs: number;
  run: () => Promise<T>;
  /** Re-evaluated after each request settles; false parks the loop. */
  shouldContinue: () => boolean;
  apply: (result: T) => void;
  onError: (error: unknown) => void;
  timers?: PollTimers;
}

export interface FilePollLoop {
  start: () => void;
  stop: () => void;
}

const defaultTimers: PollTimers = {
  setTimeout: (fn, ms) => window.setTimeout(fn, ms),
  clearTimeout: (handle) => window.clearTimeout(handle),
};

/**
 * #335: one request at a time, and only schedule the next poll once that
 * request has settled. A fixed interval stacks requests when the list scan
 * is slower than the interval.
 *
 * Responses that arrive after `stop()` (task switch, hidden pane, retry) are
 * dropped by generation. This drops only the browser-side response — it does
 * not cancel a scan already running on the server.
 */
export function createFilePollLoop<T>({
  intervalMs,
  run,
  shouldContinue,
  apply,
  onError,
  timers = defaultTimers,
}: FilePollLoopOptions<T>): FilePollLoop {
  let generation = 0;
  let running = false;
  let timer: number | null = null;

  const owns = (mine: number) => running && mine === generation;

  const runOnce = async (mine: number): Promise<void> => {
    timer = null;
    try {
      const result = await run();
      if (!owns(mine)) return;
      apply(result);
    } catch (error) {
      if (!owns(mine)) return;
      onError(error);
    }

    if (!owns(mine) || !shouldContinue()) return;
    timer = timers.setTimeout(() => {
      void runOnce(mine);
    }, intervalMs);
  };

  return {
    start() {
      if (running) return;
      running = true;
      generation += 1;
      void runOnce(generation);
    },
    stop() {
      running = false;
      generation += 1;
      if (timer !== null) timers.clearTimeout(timer);
      timer = null;
    },
  };
}
