import { Suspense } from "react";
import { ScreenerView } from "@/features/screener/screener-view";

export const metadata = { title: "選股 — Quant Signal" };

export default function ScreenerPage() {
  return (
    <Suspense fallback={null}>
      <ScreenerView />
    </Suspense>
  );
}
