"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { addToWatchlist, getWatchlist, removeFromWatchlist } from "./api/endpoints";
import { ApiError, isAbortError } from "./api/errors";
import { useSession } from "./api/session";
import type { Watchlist } from "./api/types";

/** The watchlist is stored per Google account; other sign-in methods cannot use it. */
export const WATCHLIST_NEEDS_GOOGLE =
  "自選股需要以 Google 帳號登入才能使用；目前的登入方式無法儲存自選股。";

/** Shown wherever the list or the star appears, because ETFs are not covered yet. */
export const WATCHLIST_COVERAGE_NOTE = "目前僅收錄上市、上櫃股票，不含 ETF。";

export type WatchlistPhase = "loading" | "ready" | "unavailable" | "error";

export type WatchlistToggleResult = { ok: true } | { ok: false; message: string };

type State = { phase: WatchlistPhase; data: Watchlist | null; message: string | null };

type WatchlistContextValue = {
  phase: WatchlistPhase;
  /** Why the list is unavailable / failed (the server's Chinese detail when it sent one). */
  message: string | null;
  watchlist: Watchlist | null;
  count: number;
  limit: number;
  /** Symbols with a request in flight (upper case). */
  pending: ReadonlySet<string>;
  has: (symbol: string) => boolean;
  /** Add (`member: true`) or remove (`false`) a stock. Never throws. */
  setMember: (symbol: string, member: boolean) => Promise<WatchlistToggleResult>;
  /** Re-fetch quietly; skipped when the list was loaded less than `maxAgeMs` ago. */
  refresh: (maxAgeMs?: number) => void;
  /** Re-fetch after a failure, showing the loading state again. */
  retry: () => void;
};

const DEFAULT_LIMIT = 25;
const EMPTY: ReadonlySet<string> = new Set();

const WatchlistContext = createContext<WatchlistContextValue | null>(null);

function failureState(error: unknown, previous: State): State {
  if (error instanceof ApiError && error.status === 403) {
    return { phase: "unavailable", data: null, message: error.message };
  }
  return {
    phase: "error",
    data: previous.data,
    message: error instanceof Error ? error.message : "無法取得自選股",
  };
}

/**
 * One watchlist per page load, shared by the star buttons, the tab count and
 * the 自選股 page. Fetched once after sign-in; every mutation answers with the
 * full updated list, which replaces the local copy (no refetch needed).
 */
export function WatchlistProvider({ children }: { children: ReactNode }) {
  const { user } = useSession();
  const userId = user?.id ?? null;
  const [state, setState] = useState<State>({ phase: "loading", data: null, message: null });
  const [pending, setPending] = useState<ReadonlySet<string>>(EMPTY);
  const [attempt, setAttempt] = useState(0);
  const loadedAt = useRef(0);

  useEffect(() => {
    if (userId === null) return;
    const controller = new AbortController();
    getWatchlist({ signal: controller.signal })
      .then((data) => {
        loadedAt.current = Date.now();
        setState({ phase: "ready", data, message: null });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        setState((previous) => failureState(error, previous));
      });
    return () => controller.abort();
  }, [userId, attempt]);

  const memberSymbols = useMemo(
    () => new Set((state.data?.items ?? []).map((item) => item.symbol.toUpperCase())),
    [state.data],
  );

  const has = useCallback((symbol: string) => memberSymbols.has(symbol.toUpperCase()), [memberSymbols]);

  const setMember = useCallback(
    async (symbol: string, member: boolean): Promise<WatchlistToggleResult> => {
      const key = symbol.toUpperCase();
      setPending((current) => new Set(current).add(key));
      try {
        const data = await (member ? addToWatchlist(key) : removeFromWatchlist(key));
        loadedAt.current = Date.now();
        setState({ phase: "ready", data, message: null });
        return { ok: true };
      } catch (error) {
        const message = error instanceof Error ? error.message : "自選股更新失敗，請稍後再試。";
        if (error instanceof ApiError && error.status === 403) {
          setState({ phase: "unavailable", data: null, message });
        }
        return { ok: false, message };
      } finally {
        setPending((current) => {
          const next = new Set(current);
          next.delete(key);
          return next;
        });
      }
    },
    [],
  );

  const refresh = useCallback((maxAgeMs = 0) => {
    if (Date.now() - loadedAt.current < maxAgeMs) return;
    setAttempt((n) => n + 1);
  }, []);

  const retry = useCallback(() => {
    setState((previous) => ({ ...previous, phase: "loading", message: null }));
    setAttempt((n) => n + 1);
  }, []);

  const value = useMemo<WatchlistContextValue>(() => {
    // Password / API-key sessions have no Google identity: do not even ask the server.
    const signedOut = userId === null;
    return {
      phase: signedOut ? "unavailable" : state.phase,
      message: signedOut ? WATCHLIST_NEEDS_GOOGLE : state.message,
      watchlist: state.data,
      count: state.data?.count ?? 0,
      limit: state.data?.limit ?? DEFAULT_LIMIT,
      pending,
      has,
      setMember,
      refresh,
      retry,
    };
  }, [userId, state, pending, has, setMember, refresh, retry]);

  return <WatchlistContext.Provider value={value}>{children}</WatchlistContext.Provider>;
}

export function useWatchlist(): WatchlistContextValue {
  const value = useContext(WatchlistContext);
  if (!value) throw new Error("useWatchlist must be used inside <WatchlistProvider>");
  return value;
}
