"use client";

import { useDashboard } from "@/lib/dashboard-context";
import { useScrollIntoViewOnStart } from "@/lib/use-scroll-into-view";
import { BacktestForm } from "./backtest-form";
import { BacktestResult } from "./backtest-result";

export function BacktestView() {
  const { backtest } = useDashboard();
  useScrollIntoViewOnStart(backtest.loading, "backtest-result");
  return (
    <>
      <div className="view-heading">
        <h1>策略回測</h1>
        <p>驗證訊號，不替歷史找答案：point-in-time、隔日開盤成交、計入成本。</p>
      </div>
      <div className="workbench">
        <BacktestForm />
        <div id="backtest-result" className="scroll-target">
          <BacktestResult />
        </div>
      </div>
    </>
  );
}
