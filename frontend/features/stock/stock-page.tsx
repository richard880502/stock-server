"use client";

import { SignalDetail } from "@/features/signal/signal-view";

/** One stock's page at `/stock/<symbol>`. `symbol` is null when the path is not a stock code. */
export function StockPage({ symbol }: { symbol: string | null }) {
  if (!symbol) return <p className="note">查無此股票代號。</p>;
  return <SignalDetail />;
}
