"use client";

import { useDashboard } from "@/lib/dashboard-context";
import { ForecastPanel } from "./forecast-panel";

export function ForecastView() {
  const { query } = useDashboard();
  return (
    <>
      <div className="view-heading">
        <h1>預測實驗室</h1>
        <p>看見可能的路徑，而不是單一答案：情境區間加上逐期驗證。</p>
      </div>
      {/* Remount (and clear the report) whenever the shared symbol or date changes. */}
      <ForecastPanel
        key={`${query.symbol}|${query.asOf}`}
        symbol={query.symbol}
        asOf={query.asOf}
      />
    </>
  );
}
