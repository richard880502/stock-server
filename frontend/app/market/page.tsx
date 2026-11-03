import { Suspense } from "react";
import { MarketView } from "@/features/market/market-view";

export const metadata = { title: "市場 — Quant Signal" };

export default function MarketPage() {
  return (
    <Suspense fallback={null}>
      <MarketView />
    </Suspense>
  );
}
