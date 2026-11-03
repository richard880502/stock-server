import { isAbortError, SyncPendingError, SyncTimeoutError } from "./api/errors";

/** Total tries (first request included) before giving up. */
export const SYNC_MAX_ATTEMPTS = 10;
const DEFAULT_RETRY_SECONDS = 5;
const MIN_RETRY_SECONDS = 1;
const MAX_RETRY_SECONDS = 60;

export type SyncWait = {
  symbol: string | null;
  /** Message sent by the backend (Traditional Chinese). */
  message: string;
  /** Tries made so far. */
  attempt: number;
  maxAttempts: number;
  secondsLeft: number;
};

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * Run `task`; while it fails with SyncPendingError wait for the Retry-After
 * period (reported through onWait once per second) and run it again, up to
 * maxAttempts tries. Other errors propagate immediately.
 */
export async function withSyncRetry<T>(
  task: (signal: AbortSignal) => Promise<T>,
  options: {
    signal: AbortSignal;
    onWait: (wait: SyncWait | null) => void;
    maxAttempts?: number;
  },
): Promise<T> {
  const { signal, onWait, maxAttempts = SYNC_MAX_ATTEMPTS } = options;
  for (let attempt = 1; ; attempt += 1) {
    try {
      const value = await task(signal);
      onWait(null);
      return value;
    } catch (error) {
      if (!(error instanceof SyncPendingError) || signal.aborted || isAbortError(error)) {
        onWait(null);
        throw error;
      }
      if (attempt >= maxAttempts) {
        onWait(null);
        throw new SyncTimeoutError(attempt, error.symbol);
      }
      const seconds = Math.min(
        MAX_RETRY_SECONDS,
        Math.max(MIN_RETRY_SECONDS, error.retryAfterSeconds ?? DEFAULT_RETRY_SECONDS),
      );
      try {
        for (let left = seconds; left > 0; left -= 1) {
          onWait({
            symbol: error.symbol,
            message: error.message,
            attempt,
            maxAttempts,
            secondsLeft: left,
          });
          await sleep(1_000, signal);
        }
      } catch (sleepError) {
        onWait(null);
        throw sleepError;
      }
    }
  }
}
