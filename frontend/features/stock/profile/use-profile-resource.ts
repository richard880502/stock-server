"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isAbortError } from "@/lib/api/errors";

// Successful responses per `symbol|endpoint|as_of`, so switching tabs or coming back to a
// stock never refetches. Errors are never cached: retry asks again.
const cache = new Map<string, unknown>();
const MAX_ENTRIES = 80;

export function clearProfileCache(): void {
  cache.clear();
}

export function profileKey(endpoint: string, symbol: string, asOf: string, extra = ""): string {
  return `${endpoint}|${symbol}|${asOf}${extra ? `|${extra}` : ""}`;
}

type State<T> = { key: string | null; data: T | null; error: string | null };

/**
 * Loads one profile endpoint lazily: pass `key = null` to stay idle (tab not opened yet).
 * Aborts when the key changes, caches per key, and exposes `retry()` after an error.
 */
export function useProfileResource<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
  fallbackMessage = "無法取得資料",
) {
  const [state, setState] = useState<State<T>>({ key: null, data: null, error: null });
  const [attempt, setAttempt] = useState(0);
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  });

  useEffect(() => {
    if (key === null || cache.has(key)) return;
    const controller = new AbortController();
    loadRef
      .current(controller.signal)
      .then((data) => {
        if (cache.size >= MAX_ENTRIES) cache.delete(cache.keys().next().value as string);
        cache.set(key, data);
        setState({ key, data, error: null });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        setState({ key, data: null, error: error instanceof Error ? error.message : fallbackMessage });
      });
    return () => controller.abort();
  }, [key, attempt, fallbackMessage]);

  const retry = useCallback(() => {
    setState((current) => ({ ...current, error: null }));
    setAttempt((n) => n + 1);
  }, []);

  const cached = key !== null && cache.has(key) ? (cache.get(key) as T) : null;
  const own = state.key === key ? state : null;
  const data = cached ?? own?.data ?? null;
  const error = data === null ? (own?.error ?? null) : null;
  return { data, error, loading: key !== null && data === null && error === null, retry };
}
