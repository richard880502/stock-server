import { Suspense } from "react";
import { WatchlistPage } from "@/features/watchlist/watchlist-page";

export const metadata = { title: "自選股 — Quant Signal" };

export default function Page() {
  return (
    <Suspense fallback={null}>
      <WatchlistPage />
    </Suspense>
  );
}
