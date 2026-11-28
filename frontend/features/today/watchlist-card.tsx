"use client";

import Link from "next/link";
import { Sparkline } from "@/components/charts/sparkline";
import { Card } from "@/components/card";
import { Direction } from "@/components/direction";
import { EmptyState } from "@/components/empty-state";
import { StarIcon } from "@/components/icons";
import type { WatchlistPhase } from "@/lib/use-watchlist";
import { useWatchlist } from "@/lib/use-watchlist";
import { useDashboard } from "@/lib/dashboard-context";
import type { RecentSymbol } from "@/lib/recent-symbols";
import { formatPrice, formatSigned, directionOf } from "@/lib/format";
import { stockHref } from "@/lib/stock-routes";
import type { WatchlistItem } from "@/lib/api/types";
import { CardError, CardSkeleton, MoreLink } from "./today-parts";
import { type WatchRow, watchRows, WATCHLIST_ROWS } from "./today-model";

export function WatchRowLink({ row }: { row: WatchRow }) {
  const tone = row.changePct === null ? "flat" : directionOf(row.changePct);
  return (
    <li>
      <Link href={stockHref(row.symbol)} className="today-row" aria-label={`${row.name || row.code}（${row.code}）個股頁`}>
        <span className="today-row-id">
          <span className="today-code mono">{row.code}</span>
          <span className="today-name">{row.name}</span>
        </span>
        <span className="today-spark">
          <Sparkline values={row.closes} width={64} height={22} label={`${row.name || row.code} 近期收盤走勢`} />
        </span>
        <span className={`today-price num ${tone === "flat" ? "" : `text-${tone}`}`}>
          {row.price === null ? "—" : formatPrice(row.price)}
        </span>
        <span className="today-change num">
          {row.changePct === null ? (
            <span className="text-muted">—</span>
          ) : (
            <Direction value={row.changePct}>{formatSigned(row.changePct, 2)}%</Direction>
          )}
        </span>
      </Link>
    </li>
  );
}

export function RecentList({ recent }: { recent: RecentSymbol[] }) {
  return (
    <ul className="today-chips">
      {recent.map((item) => (
        <li key={item.symbol}>
          <Link href={stockHref(item.symbol)} className="today-chip">
            <span className="mono">{item.symbol.replace(/\.TWO?$/, "")}</span>
            {item.name && <span>{item.name}</span>}
          </Link>
        </li>
      ))}
    </ul>
  );
}

export type WatchlistBodyProps = {
  phase: WatchlistPhase;
  message: string | null;
  items: WatchlistItem[] | null;
  count: number;
  recent: RecentSymbol[];
  onRetry: () => void;
};

/** Everything inside the card; separate from the context hooks so each branch can be rendered in tests. */
export function WatchlistBody({ phase, message, items, count, recent, onRetry }: WatchlistBodyProps) {
  if (phase === "loading") return <CardSkeleton label="自選股載入中" lines={5} />;
  if (phase === "error") return <CardError message={message ?? "無法取得自選股"} onRetry={onRetry} />;
  if (phase === "unavailable") {
    return recent.length > 0 ? (
      <>
        <p className="note">自選股需以 Google 帳號登入才能使用；先列出你最近看過的股票。</p>
        <RecentList recent={recent} />
      </>
    ) : (
      <EmptyState icon={<StarIcon size={22} />} title="登入後可使用自選股">
        自選股需以 Google 帳號登入；也可以按 ⌘K 搜尋股票。
      </EmptyState>
    );
  }
  const rows = watchRows(items, WATCHLIST_ROWS);
  if (rows.length === 0) {
    return (
      <EmptyState
        icon={<StarIcon size={22} />}
        title="還沒有自選股"
        action={
          <Link href="/screener" className="button small">
            去選股
          </Link>
        }
      >
        在個股頁按星號加入；按 ⌘K 可以搜尋股票。
      </EmptyState>
    );
  }
  return (
    <>
      <ul className="today-rows">
        {rows.map((row) => (
          <WatchRowLink key={row.symbol} row={row} />
        ))}
      </ul>
      {count > rows.length && <p className="today-meta">只顯示前 {rows.length} 檔，共 {count} 檔。</p>}
    </>
  );
}

export function WatchlistCard() {
  const watchlist = useWatchlist();
  const { recentSymbols } = useDashboard();
  return (
    <Card title="我的自選股" className="today-card today-watch">
      <div className="today-body">
        <WatchlistBody
          phase={watchlist.phase}
          message={watchlist.message}
          items={watchlist.watchlist?.items ?? null}
          count={watchlist.count}
          recent={recentSymbols}
          onRetry={watchlist.retry}
        />
      </div>
      <MoreLink href="/watchlist">管理自選股 →</MoreLink>
    </Card>
  );
}
