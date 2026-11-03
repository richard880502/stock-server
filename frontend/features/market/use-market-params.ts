"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";
import type { MarketMetric, MarketScope } from "@/lib/api/types";
import { FLOW_RANGES, isMetric, SCOPES, WINDOWS } from "./metrics";

export type MarketParams = {
  market: MarketScope;
  window: number;
  metric: MarketMetric;
  /** Days shown in the capital-flow trend. */
  range: number;
  cumulative: boolean;
};

const DEFAULTS: MarketParams = {
  market: "ALL",
  window: 1,
  metric: "total_net",
  range: 20,
  cumulative: false,
};

/**
 * The market page's own filters live in the query string (?market=TW&window=5
 * &metric=foreign_net&range=60&cum=1), so a view can be linked and survives
 * reload. Unknown or malformed values fall back to the defaults.
 */
export function useMarketParams() {
  const search = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();

  const rawMarket = search.get("market");
  const rawWindow = Number(search.get("window"));
  const rawMetric = search.get("metric");
  const rawRange = Number(search.get("range"));
  const rawCum = search.get("cum");

  const params = useMemo<MarketParams>(
    () => ({
      market: SCOPES.some((s) => s.value === rawMarket) ? (rawMarket as MarketScope) : DEFAULTS.market,
      window: (WINDOWS as readonly number[]).includes(rawWindow) ? rawWindow : DEFAULTS.window,
      metric: isMetric(rawMetric) ? rawMetric : DEFAULTS.metric,
      range: (FLOW_RANGES as readonly number[]).includes(rawRange) ? rawRange : DEFAULTS.range,
      cumulative: rawCum === "1",
    }),
    [rawMarket, rawWindow, rawMetric, rawRange, rawCum],
  );

  const update = useCallback(
    (patch: Partial<MarketParams>) => {
      const next = { ...params, ...patch };
      const query = new URLSearchParams();
      if (next.market !== DEFAULTS.market) query.set("market", next.market);
      if (next.window !== DEFAULTS.window) query.set("window", String(next.window));
      if (next.metric !== DEFAULTS.metric) query.set("metric", next.metric);
      if (next.range !== DEFAULTS.range) query.set("range", String(next.range));
      if (next.cumulative) query.set("cum", "1");
      const text = query.toString();
      router.replace(text ? `${pathname}?${text}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  return { params, update };
}
