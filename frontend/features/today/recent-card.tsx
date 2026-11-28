"use client";

import { Card } from "@/components/card";
import { RecentList } from "./watchlist-card";
import type { RecentSymbol } from "@/lib/recent-symbols";

export function RecentCard({ recent }: { recent: RecentSymbol[] }) {
  if (recent.length === 0) return null;
  return (
    <Card title="最近看過" className="today-card today-recent">
      <RecentList recent={recent} />
    </Card>
  );
}
