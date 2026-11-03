"use client";

import { BacktestForm } from "./backtest-form";
import { BacktestResult } from "./backtest-result";

export function BacktestView() {
  return (
    <>
      <div className="view-heading">
        <h1>策略回測</h1>
        <p>驗證訊號，不替歷史找答案：point-in-time、隔日開盤成交、計入成本。</p>
      </div>
      <div className="workbench">
        <BacktestForm />
        <BacktestResult />
      </div>
    </>
  );
}
