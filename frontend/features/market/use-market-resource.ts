"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isAbortError } from "@/lib/api/errors";

/** While the server is still downloading history, ask again this often. */
export const BACKFILL_POLL_MS = 15_000;

type State<T> = { key: string | null; data: T | null; error: string | null };

/**
 * Loads one market endpoint for a cache `key` (the serialised query). Switching
 * the key keeps the previous data around as `stale` so the page can dim it
 * instead of blanking; `retry()` reloads after an error. While the response says
 * `backfill_pending` it silently re-fetches every 15 seconds.
 */
export function useMarketResource<T extends { backfill_pending: boolean }>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
) {
  const [state, setState] = useState<State<T>>({ key: null, data: null, error: null });
  const [attempt, setAttempt] = useState(0);
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  });

  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const run = async () => {
      try {
        const data = await loadRef.current(controller.signal);
        setState({ key, data, error: null });
        if (data.backfill_pending) timer = window.setTimeout(run, BACKFILL_POLL_MS);
      } catch (error) {
        if (controller.signal.aborted || isAbortError(error)) return;
        const message = error instanceof Error ? error.message : "無法取得市場資料";
        setState((previous) => ({
          key,
          data: previous.key === key ? previous.data : null,
          error: message,
        }));
      }
    };
    void run();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [key, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  const current = state.key === key;
  return {
    data: state.data,
    /** The data belongs to an earlier query and a newer one is loading. */
    stale: key !== null && !current && state.data !== null && state.error === null,
    loading: key !== null && (!current || (state.data === null && state.error === null)),
    error: current ? state.error : null,
    retry,
  };
}
