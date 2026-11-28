import { Suspense } from "react";
import { BacktestView } from "@/features/backtest/backtest-view";

export const metadata = { title: "策略回測 — Quant Signal" };

export default function BacktestPage() {
  return (
    <Suspense fallback={null}>
      <BacktestView />
    </Suspense>
  );
}
