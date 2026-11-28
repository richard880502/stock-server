"use client";

import { usePathname } from "next/navigation";
import {
  createContext,
  type ReactNode,
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
import { symbolFromPath } from "./stock-routes";
import { getRecentSymbols, type RecentSymbol, rememberSymbol } from "./recent-symbols";
import {
  benchmarkForSymbol,
  DEFAULT_BENCHMARK,
  DEFAULT_SYMBOL,
  marketOf,
} from "./symbols";

function useDashboardState() {
  // On a stock page (`/stock/<symbol>`) the path names the stock; see the route-sync effect below.
  const pathname = usePathname();
  const routeSymbol = symbolFromPath(pathname);
  const [startSymbol] = useState(() => routeSymbol ?? DEFAULT_SYMBOL);
  const [symbol, setSymbolState] = useState(startSymbol);
  const [benchmark, setBenchmark] = useState(() => benchmarkForSymbol(startSymbol, DEFAULT_BENCHMARK));
  // False while the symbol is only the built-in default nobody chose (a fresh load of 今日總覽,
  // 市場…): such a symbol is not added to 最近看過.
  const [picked, setPicked] = useState(routeSymbol !== null);
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
  const needsSignal = routeUsesSignalData(pathname);
  const needsSignalRef = useRef(needsSignal);
  const [initialQuery] = useState<SignalQuery>(() => ({
    symbol: startSymbol,
    benchmark: benchmarkForSymbol(startSymbol, DEFAULT_BENCHMARK),
    asOf: today(),
    market: marketOf(startSymbol),
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
    if (overrides.symbol) setPicked(true);
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
    // A stock page for another symbol was just opened: the route-sync effect below loads it.
    if (routeSymbol && pendingSignal.symbol !== routeSymbol) return;
    const parked = pendingSignal;
    void load(parked);
    // Cleared from a callback, not synchronously: `loading` stays true until the load has begun.
    queueMicrotask(() => setPendingSignal((current) => (current === parked ? null : current)));
  }, [needsSignal, pendingSignal, load, routeSymbol]);

  // The stock page's path is the source of truth for the symbol: opening `/stock/<symbol>`
  // (link, palette, back/forward) switches the shared query to it. Keyed on the path only, so
  // it never fights a symbol chosen through `refresh` on other routes.
  const refreshRef = useRef(refresh);
  useEffect(() => {
    refreshRef.current = refresh;
  });
  const symbolRef = useRef(symbol);
  useEffect(() => {
    symbolRef.current = symbol;
  }, [symbol]);
  useEffect(() => {
    if (routeSymbol && routeSymbol !== symbolRef.current) refreshRef.current({ symbol: routeSymbol });
  }, [routeSymbol]);

  // Recent symbols live in localStorage, so they can only be read after mount.
  useEffect(() => {
    const timer = window.setTimeout(() => setRecentSymbols(getRecentSymbols()), 0);
    return () => window.clearTimeout(timer);
  }, []);

  // Once a symbol's signal has loaded, resolve its name and remember it as recent. Routes that
  // skip the signal (回測, 預測) resolve the name of the query's symbol instead — a signal left
  // over from an earlier stock page must not stand in for it. A symbol is remembered only when
  // it is shown on a signal route or was chosen, never the untouched default.
  const loadedSymbol = needsSignal ? (signalData.signal?.symbol ?? null) : symbol;
  const remember = needsSignal || picked;
  useEffect(() => {
    if (!loadedSymbol) return;
    const controller = new AbortController();
    searchInstruments(loadedSymbol, { signal: controller.signal })
      .then((rows) => {
        const name = rows.find((row) => row.symbol === loadedSymbol)?.name;
        setInstrumentName({ symbol: loadedSymbol, name: name ?? "" });
        if (remember) setRecentSymbols(rememberSymbol({ symbol: loadedSymbol, name }));
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setInstrumentName({ symbol: loadedSymbol, name: "" });
        if (remember) setRecentSymbols(rememberSymbol({ symbol: loadedSymbol }));
      });
    return () => controller.abort();
  }, [loadedSymbol, remember]);

  useEffect(() => {
    getLlmStatus().then(setLlmStatus).catch(() => setLlmStatus(null));
    getDataStatus().then(setDataStatus).catch(() => setDataStatus(null));
  }, []);

  return {
    query,
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
