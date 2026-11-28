import { Suspense } from "react";
import { ForecastView } from "@/features/forecast/forecast-view";

export const metadata = { title: "預測實驗室 — Quant Signal" };

export default function ForecastPage() {
  return (
    <Suspense fallback={null}>
      <ForecastView />
    </Suspense>
  );
}
