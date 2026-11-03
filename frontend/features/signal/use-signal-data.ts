"use client";

import { useCallback, useState } from "react";
import {
  getChipFlow,
  getMarketEnvironment,
  getSignal,
  type MarketCode,
} from "@/lib/api/endpoints";
import type { ChipFlow, MarketEnvironment, Signal } from "@/lib/api/types";
import { useSyncRetry } from "@/lib/use-sync-retry";

export type SignalQuery = {
  symbol: string;
  benchmark: string;
  asOf: string;
  market: MarketCode;
};

/**
 * Loads the symbol signal, market environment and chip flow together. When the
 * backend answers 503 sync_pending the load is retried automatically and the
 * countdown is exposed as `syncWait`.
 */
export function useSignalData(onError: (message: string | null) => void) {
  const [signal, setSignal] = useState<Signal | null>(null);
  const [marketEnvironment, setMarketEnvironment] =
    useState<MarketEnvironment | null>(null);
  const [chipFlow, setChipFlow] = useState<ChipFlow | null>(null);
  const sync = useSyncRetry();
  const { run } = sync;

  const load = useCallback(
    async (query: SignalQuery) => {
      onError(null);
      try {
        const result = await run(
          async (abort) => {
            const init = { signal: abort };
            return Promise.all([
              getSignal(query, init),
              getMarketEnvironment(query.market, query.asOf, init),
              getChipFlow(query.symbol, query.asOf, init).catch((error) => {
                if (abort.aborted) throw error;
                return null;
              }),
            ]);
          },
          {
            // Stale data for another symbol is misleading while we wait.
            onWait: () => {
              setSignal(null);
              setMarketEnvironment(null);
              setChipFlow(null);
            },
          },
        );
        if (result.aborted) return;
        const [nextSignal, nextMarket, nextChip] = result.value;
        setSignal(nextSignal);
        setMarketEnvironment(nextMarket);
        setChipFlow(nextChip);
      } catch (error) {
        setSignal(null);
        setMarketEnvironment(null);
        setChipFlow(null);
        onError(error instanceof Error ? error.message : "無法取得量化訊號");
      }
    },
    [onError, run],
  );

  return {
    signal,
    marketEnvironment,
    chipFlow,
    loading: sync.busy,
    syncWait: sync.wait,
    cancelSync: sync.cancel,
    load,
  };
}
