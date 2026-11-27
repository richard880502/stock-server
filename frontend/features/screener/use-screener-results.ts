"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isAbortError } from "@/lib/api/errors";
import type { ScreenerEntry, ScreenerResponse } from "@/lib/api/types";

/** Requests wait this long after the last change, so typing or dragging does not fire one per step. */
export const DEBOUNCE_MS = 300;
/** While the server has no snapshot yet (`snapshot_pending`), ask again this often. */
export const PENDING_POLL_MS = 30_000;

type State = { key: string | null; head: ScreenerResponse | null; entries: ScreenerEntry[]; error: string | null };

const messageOf = (error: unknown) => (error instanceof Error ? error.message : "無法取得選股結果");

/**
 * One screener result list for a cache `key` (the serialised query without paging).
 * A new key debounces, aborts the request in flight and keeps the old rows around as `stale`;
 * `loadMore()` appends the next page; `snapshot_pending` re-fetches every 30 seconds.
 */
export function useScreenerResults(
  key: string | null,
  load: (offset: number, signal: AbortSignal) => Promise<ScreenerResponse>,
) {
  const [state, setState] = useState<State>({ key: null, head: null, entries: [], error: null });
  const [attempt, setAttempt] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  const loadRef = useRef(load);
  const stateRef = useRef(state);
  const moreController = useRef<AbortController | null>(null);
  useEffect(() => {
    loadRef.current = load;
    stateRef.current = state;
  });

  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const run = async () => {
      try {
        const data = await loadRef.current(0, controller.signal);
        setState({ key, head: data, entries: data.entries, error: null });
        if (data.snapshot_pending) timer = window.setTimeout(run, PENDING_POLL_MS);
      } catch (error) {
        if (controller.signal.aborted || isAbortError(error)) return;
        setState((previous) => ({
          key,
          head: previous.key === key ? previous.head : null,
          entries: previous.key === key ? previous.entries : [],
          error: messageOf(error),
        }));
      }
    };
    // The first list (and a retry) goes out at once; later edits are debounced.
    timer = window.setTimeout(run, stateRef.current.head === null ? 0 : DEBOUNCE_MS);
    return () => {
      controller.abort();
      moreController.current?.abort();
      setLoadingMore(false);
      window.clearTimeout(timer);
    };
  }, [key, attempt]);

  const loadMore = useCallback(async () => {
    const current = stateRef.current;
    if (!current.head || current.key === null || moreController.current) return;
    const controller = new AbortController();
    moreController.current = controller;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const data = await loadRef.current(current.entries.length, controller.signal);
      setState((previous) => {
        if (previous.key !== current.key) return previous;
        const seen = new Set(previous.entries.map((entry) => entry.symbol));
        const added = data.entries.filter((entry) => !seen.has(entry.symbol));
        return { ...previous, head: data, entries: [...previous.entries, ...added] };
      });
    } catch (error) {
      if (!controller.signal.aborted && !isAbortError(error)) setMoreError(messageOf(error));
    } finally {
      if (moreController.current === controller) {
        moreController.current = null;
        setLoadingMore(false);
      }
    }
  }, []);

  const retry = useCallback(() => {
    setMoreError(null);
    setAttempt((n) => n + 1);
  }, []);

  const current = state.key === key;
  return {
    data: state.head,
    entries: state.entries,
    /** The rows answer an earlier query and a newer one is loading. */
    stale: key !== null && !current && state.head !== null && state.error === null,
    loading: key !== null && (!current || (state.head === null && state.error === null)),
    error: current ? state.error : null,
    loadingMore,
    moreError,
    hasMore: current && state.head !== null && state.entries.length < state.head.matched,
    loadMore,
    retry,
  };
}
