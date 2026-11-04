"use client";

import { useEffect, useState } from "react";
import { getStockOverview } from "@/lib/api/endpoints";
import { ApiError, isAbortError } from "@/lib/api/errors";
import type { StockOverview } from "@/lib/api/types";

type State = {
  symbol: string | null;
  data: StockOverview | null;
  error: { message: string; status: number | null } | null;
};

/**
 * Loads the snapshot-based quick view of `symbol`. Changing `refreshKey`
 * re-fetches in the background and keeps the previous data on screen; a
 * different symbol never shows the old symbol's numbers.
 */
export function useStockOverview(symbol: string, refreshKey: number) {
  const [state, setState] = useState<State>({ symbol: null, data: null, error: null });

  useEffect(() => {
    const controller = new AbortController();
    getStockOverview(symbol, { signal: controller.signal })
      .then((data) => setState({ symbol, data, error: null }))
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        const failure = {
          message: error instanceof Error ? error.message : "無法取得快速檢視資料",
          status: error instanceof ApiError ? error.status : null,
        };
        setState((previous) => ({
          symbol,
          data: previous.symbol === symbol ? previous.data : null,
          error: failure,
        }));
      });
    return () => controller.abort();
  }, [symbol, refreshKey]);

  const current = state.symbol === symbol;
  const data = current ? state.data : null;
  const error = current ? state.error : null;
  return { data, error, loading: !current || (data === null && error === null) };
}
