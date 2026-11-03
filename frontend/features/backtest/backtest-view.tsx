"use client";

import { BacktestForm } from "./backtest-form";
import { BacktestResult } from "./backtest-result";

export function BacktestView() {
  return (
    <section className="backtest-layout">
      <BacktestForm />
      <BacktestResult />
    </section>
  );
}
