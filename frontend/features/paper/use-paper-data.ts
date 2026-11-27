"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, isAbortError } from "@/lib/api/errors";
import type { Page } from "@/lib/api/paper";

export type ResourceError = { message: string; status: number | null };

const toError = (error: unknown, fallback: string): ResourceError => ({
  message: error instanceof Error && error.message ? error.message : fallback,
  status: error instanceof ApiError ? error.status : null,
});

type State<T> = { key: string | null; data: T | null; error: ResourceError | null };

/**
 * One GET for a cache `key`. Switching the key keeps the previous data as `stale` (dim it,
 * do not blank it); `pollMs` re-fetches quietly in the background while the tab is visible;
 * `reload()` forces a fetch now (after an order, say). A failed background poll keeps the
 * last good data and sets `error`.
 */
export function usePolled<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
  pollMs: number | null = null,
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
      } catch (error) {
        if (controller.signal.aborted || isAbortError(error)) return;
        setState((previous) => ({
          key,
          data: previous.key === key ? previous.data : null,
          error: toError(error, "無法取得資料"),
        }));
      }
      if (pollMs && !controller.signal.aborted) timer = window.setTimeout(tick, pollMs);
    };
    const tick = () => {
      // A hidden tab only polls again once it is visible.
      if (document.hidden) timer = window.setTimeout(tick, 1000);
      else void run();
    };
    void run();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [key, attempt, pollMs]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  const current = state.key === key;
  return {
    data: state.data,
    /** The data belongs to an earlier key and the new one is loading. */
    stale: key !== null && !current && state.data !== null,
    loading: key !== null && (!current || (state.data === null && state.error === null)),
    error: current ? state.error : null,
    reload,
  };
}

type PagedState<T> = {
  key: string | null;
  items: T[];
  hasMore: boolean;
  error: ResourceError | null;
  loadingMore: boolean;
};

/** An offset-paged list with 載入更多. `reload()` refetches the first page and replaces the list. */
export function usePagedList<T>(
  key: string | null,
  fetchPage: (offset: number, limit: number, signal: AbortSignal) => Promise<Page<T>>,
  pageSize = 20,
) {
  const [state, setState] = useState<PagedState<T>>({
    key: null,
    items: [],
    hasMore: false,
    error: null,
    loadingMore: false,
  });
  const [attempt, setAttempt] = useState(0);
  const fetchRef = useRef(fetchPage);
  useEffect(() => {
    fetchRef.current = fetchPage;
  });
  const moreController = useRef<AbortController | null>(null);

  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    moreController.current?.abort();
    fetchRef
      .current(0, pageSize, controller.signal)
      .then((page) =>
        setState({
          key,
          items: page.items,
          hasMore: page.hasMore ?? page.items.length >= pageSize,
          error: null,
          loadingMore: false,
        }),
      )
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        setState((previous) => ({
          key,
          items: previous.key === key ? previous.items : [],
          hasMore: false,
          error: toError(error, "無法取得資料"),
          loadingMore: false,
        }));
      });
    return () => controller.abort();
  }, [key, attempt, pageSize]);

  const items = state.key === key ? state.items : [];
  const itemCount = items.length;
  const loadMore = useCallback(() => {
    if (key === null) return;
    const controller = new AbortController();
    moreController.current = controller;
    setState((previous) => ({ ...previous, loadingMore: true, error: null }));
    fetchRef
      .current(itemCount, pageSize, controller.signal)
      .then((page) =>
        setState((previous) =>
          previous.key !== key
            ? previous
            : {
                ...previous,
                items: [...previous.items, ...page.items],
                hasMore: page.hasMore ?? page.items.length >= pageSize,
                loadingMore: false,
              },
        ),
      )
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        setState((previous) => ({ ...previous, loadingMore: false, error: toError(error, "無法取得資料") }));
      });
  }, [key, itemCount, pageSize]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  const current = state.key === key;
  return {
    items,
    hasMore: current && state.hasMore,
    loading: key !== null && !current,
    loadingMore: current && state.loadingMore,
    error: current ? state.error : null,
    loadMore,
    reload,
  };
}
