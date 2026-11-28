"use client";

import { usePathname } from "next/navigation";
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
import { useBacktest } from "@/features/backtest/use-backtest";
import { useLlmAnalysis } from "@/features/llm/use-llm-analysis";
import { type SignalQuery, useSignalData } from "@/features/signal/use-signal-data";
import { getDataStatus, getLlmStatus, searchInstruments } from "./api/endpoints";
import type { DataStatus, LlmStatus } from "./api/types";
import { today } from "./format";
import { routeUsesSignalData } from "./signal-routes";
import { getRecentSymbols, type RecentSymbol, rememberSymbol } from "./recent-symbols";
import {
  benchmarkForSymbol,
  DEFAULT_BENCHMARK,
  DEFAULT_SYMBOL,
  marketOf,
} from "./symbols";

function useDashboardState() {
  const [symbol, setSymbolState] = useState(DEFAULT_SYMBOL);
  const [benchmark, setBenchmark] = useState(DEFAULT_BENCHMARK);
  const [asOf, setAsOf] = useState(today);
  const [error, setError] = useState<string | null>(null);
  const [llmStatus, setLlmStatus] = useState<LlmStatus | null>(null);
  const [dataStatus, setDataStatus] = useState<DataStatus | null>(null);

  const market = marketOf(symbol);
  const query = useMemo(
    () => ({ symbol, benchmark, asOf, market }),
    [symbol, benchmark, asOf, market],
  );

  const signalLoader = useSignalData(setError);
  // The signal, chip flow and market environment are fetched only on routes that show them
  // (see lib/signal-routes.ts). On other routes a requested load is parked in `pendingSignal`
  // and runs when the user reaches such a route.
  const needsSignal = routeUsesSignalData(usePathname());
  const needsSignalRef = useRef(needsSignal);
  const [initialQuery] = useState<SignalQuery>(() => ({
    symbol: DEFAULT_SYMBOL,
    benchmark: DEFAULT_BENCHMARK,
    asOf: today(),
    market: marketOf(DEFAULT_SYMBOL),
  }));
  const [pendingSignal, setPendingSignal] = useState<SignalQuery | null>(initialQuery);
  const signalData = {
    ...signalLoader,
    loading: signalLoader.loading || (needsSignal && pendingSignal !== null),
  };
  const backtest = useBacktest(query, setError);
  const llm = useLlmAnalysis(
    query,
    backtest.job?.status === "completed" ? backtest.job.id : null,
  );

  const [recentSymbols, setRecentSymbols] = useState<RecentSymbol[]>([]);
  const [instrumentName, setInstrumentName] = useState<{ symbol: string; name: string } | null>(
    null,
  );

  const setSymbol = useCallback((next: string) => {
    const upper = next.toUpperCase();
    setSymbolState(upper);
    setBenchmark((current) => benchmarkForSymbol(upper, current));
  }, []);

  const { load } = signalLoader;
  const { reset: resetBacktest } = backtest;
  const { resetStream } = llm;

  /**
   * Reload the signal and drop results derived from the old query. `overrides`
   * commits new values in the same step (the combobox picks a symbol and loads
   * it without waiting for a re-render).
   */
  const refresh = (
    overrides: Partial<{ symbol: string; benchmark: string; asOf: string }> = {},
  ) => {
    const nextSymbol = (overrides.symbol ?? symbol).trim().toUpperCase();
    const nextBenchmark =
      overrides.benchmark ?? benchmarkForSymbol(nextSymbol, benchmark);
    const nextAsOf = overrides.asOf ?? asOf;
    setSymbolState(nextSymbol);
    setBenchmark(nextBenchmark);
    setAsOf(nextAsOf);
    resetStream();
    resetBacktest();
    const next: SignalQuery = {
      symbol: nextSymbol,
      benchmark: nextBenchmark,
      asOf: nextAsOf,
      market: marketOf(nextSymbol),
    };
    if (needsSignalRef.current) {
      setPendingSignal(null);
      void load(next);
    } else {
      setPendingSignal(next);
    }
  };

  // First load uses the defaults, exactly as the single-page dashboard did, but only once a
  // route that shows the analysis is open; a parked query loads when its route is reached.
  useEffect(() => {
    needsSignalRef.current = needsSignal;
    if (!needsSignal || pendingSignal === null) return;
    const parked = pendingSignal;
    void load(parked);
    // Cleared from a callback, not synchronously: `loading` stays true until the load has begun.
    queueMicrotask(() => setPendingSignal((current) => (current === parked ? null : current)));
  }, [needsSignal, pendingSignal, load]);

  // Recent symbols live in localStorage, so they can only be read after mount.
  useEffect(() => {
    const timer = window.setTimeout(() => setRecentSymbols(getRecentSymbols()), 0);
    return () => window.clearTimeout(timer);
  }, []);

  // Once a symbol's signal has loaded, resolve its name and remember it as recent.
  // Routes that skip the signal still resolve the name of the symbol their query bar shows.
  const loadedSymbol = signalData.signal?.symbol ?? (needsSignal ? null : symbol);
  useEffect(() => {
    if (!loadedSymbol) return;
    const controller = new AbortController();
    searchInstruments(loadedSymbol, { signal: controller.signal })
      .then((rows) => {
        const name = rows.find((row) => row.symbol === loadedSymbol)?.name;
        setInstrumentName({ symbol: loadedSymbol, name: name ?? "" });
        setRecentSymbols(rememberSymbol({ symbol: loadedSymbol, name }));
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setInstrumentName({ symbol: loadedSymbol, name: "" });
        setRecentSymbols(rememberSymbol({ symbol: loadedSymbol }));
      });
    return () => controller.abort();
  }, [loadedSymbol]);

  useEffect(() => {
    getLlmStatus().then(setLlmStatus).catch(() => setLlmStatus(null));
    getDataStatus().then(setDataStatus).catch(() => setDataStatus(null));
  }, []);

  return {
    query: { ...query, setSymbol, setBenchmark, setAsOf },
    error,
    setError,
    llmStatus,
    dataStatus,
    recentSymbols,
    instrumentName,
    signalData,
    needsSignal,
    backtest,
    llm,
    refresh,
  };
}

export type DashboardState = ReturnType<typeof useDashboardState>;

const DashboardContext = createContext<DashboardState | null>(null);

export function DashboardProvider({ children }: { children: ReactNode }) {
  const value = useDashboardState();
  return <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>;
}

export function useDashboard(): DashboardState {
  const value = useContext(DashboardContext);
  if (!value) throw new Error("useDashboard must be used inside <DashboardProvider>");
  return value;
}
