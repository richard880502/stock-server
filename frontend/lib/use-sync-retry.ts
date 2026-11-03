"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isAbortError } from "./api/errors";
import { type SyncWait, withSyncRetry } from "./sync-retry";

export type SyncRunResult<T> = { aborted: false; value: T } | { aborted: true };

/**
 * Runs one request at a time with automatic 503 sync_pending retries.
 * Starting a new run, calling cancel() or unmounting aborts the previous run;
 * an aborted run resolves to { aborted: true } so callers can skip their
 * state updates. `busy` is true from start until the current run settles.
 */
export function useSyncRetry() {
  const [wait, setWait] = useState<SyncWait | null>(null);
  const [busy, setBusy] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setWait(null);
    setBusy(false);
  }, []);

  useEffect(() => cancel, [cancel]);

  const run = useCallback(
    async <T,>(
      task: (signal: AbortSignal) => Promise<T>,
      options: { onWait?: (wait: SyncWait) => void } = {},
    ): Promise<SyncRunResult<T>> => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      setBusy(true);
      try {
        const value = await withSyncRetry(task, {
          signal: controller.signal,
          onWait: (next) => {
            if (controllerRef.current !== controller) return;
            setWait(next);
            if (next) options.onWait?.(next);
          },
        });
        if (controllerRef.current !== controller) return { aborted: true };
        return { aborted: false, value };
      } catch (error) {
        if (controllerRef.current !== controller || isAbortError(error)) {
          return { aborted: true };
        }
        throw error;
      } finally {
        if (controllerRef.current === controller) {
          controllerRef.current = null;
          setBusy(false);
        }
      }
    },
    [],
  );

  return { wait, busy, run, cancel };
}
