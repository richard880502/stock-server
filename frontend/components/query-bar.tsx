"use client";

import { useDashboard } from "@/lib/dashboard-context";

/** Symbol / benchmark / as-of date shared by every view. */
export function QueryBar() {
  const { query, signalData, refresh } = useDashboard();
  return (
    <form
      className="query-bar"
      aria-label="分析條件"
      onSubmit={(event) => {
        event.preventDefault();
        refresh();
      }}
    >
      <label>
        <span>分析標的</span>
        <input
          value={query.symbol}
          onChange={(event) => query.setSymbol(event.target.value)}
          placeholder="2330.TW / 6488.TWO"
          autoComplete="off"
          spellCheck={false}
        />
      </label>
      <label>
        <span>比較基準</span>
        <input
          value={query.benchmark}
          onChange={(event) => query.setBenchmark(event.target.value.toUpperCase())}
          placeholder="^TWII"
          autoComplete="off"
          spellCheck={false}
        />
      </label>
      <label>
        <span>資料截止日</span>
        <input
          type="date"
          value={query.asOf}
          onChange={(event) => query.setAsOf(event.target.value)}
        />
      </label>
      <button className="primary-button" disabled={signalData.loading}>
        {signalData.loading ? "計算中…" : "更新訊號"}
      </button>
    </form>
  );
}
