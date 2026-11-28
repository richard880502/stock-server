"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useRef } from "react";
import { useDashboard } from "./dashboard-context";
import { symbolFromParam } from "./stock-routes";

/**
 * Pages that work on the shared symbol (回測, 預測) accept `?symbol=2330.TW` so links from a
 * stock page, the screener or the palette open them for that stock. The parameter is applied
 * when it appears or changes; afterwards the page's own picker is free to change the symbol.
 * Must be rendered inside a <Suspense> boundary (it reads the search params).
 */
export function useSymbolParam(): void {
  const requested = symbolFromParam(useSearchParams()?.get("symbol"));
  const { query, refresh } = useDashboard();
  const latest = useRef({ symbol: query.symbol, refresh });
  useEffect(() => {
    latest.current = { symbol: query.symbol, refresh };
  });
  useEffect(() => {
    if (requested && requested !== latest.current.symbol) latest.current.refresh({ symbol: requested });
  }, [requested]);
}
