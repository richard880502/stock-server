"use client";

import { useDashboard } from "@/lib/dashboard-context";
import { ForecastPanel } from "./forecast-panel";

export function ForecastView() {
  const { query } = useDashboard();
  // Remount (and clear the report) whenever the shared symbol or date changes.
  return (
    <ForecastPanel
      key={`${query.symbol}|${query.asOf}`}
      symbol={query.symbol}
      asOf={query.asOf}
    />
  );
}
