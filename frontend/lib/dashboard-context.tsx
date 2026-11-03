"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useBacktest } from "@/features/backtest/use-backtest";
import { useLlmAnalysis } from "@/features/llm/use-llm-analysis";
import { useSignalData } from "@/features/signal/use-signal-data";
import { getDataStatus, getLlmStatus } from "./api/endpoints";
import type { DataStatus, LlmStatus } from "./api/types";
import { today } from "./format";
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

  const signalData = useSignalData(setError);
  const backtest = useBacktest(query, setError);
  const llm = useLlmAnalysis(
    query,
    backtest.job?.status === "completed" ? backtest.job.id : null,
    setError,
  );

  const setSymbol = useCallback((next: string) => {
    const upper = next.toUpperCase();
    setSymbolState(upper);
    setBenchmark((current) => benchmarkForSymbol(upper, current));
  }, []);

  const { load } = signalData;
  const { reset: resetBacktest } = backtest;
  const { resetStream } = llm;

  /** Reload the signal for the current query and drop results derived from the old one. */
  const refresh = () => {
    resetStream();
    resetBacktest();
    void load(query);
  };

  // First load uses the defaults, exactly as the single-page dashboard did.
  const [initialQuery] = useState(() => ({
    symbol: DEFAULT_SYMBOL,
    benchmark: DEFAULT_BENCHMARK,
    asOf: today(),
    market: marketOf(DEFAULT_SYMBOL),
  }));
  useEffect(() => {
    void load(initialQuery);
  }, [load, initialQuery]);

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
    signalData,
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
