import { Suspense } from "react";
import { PaperPage } from "@/features/paper/paper-page";

export const metadata = { title: "模擬交易 — Quant Signal" };

export default function Page() {
  return (
    <Suspense fallback={null}>
      <PaperPage />
    </Suspense>
  );
}
