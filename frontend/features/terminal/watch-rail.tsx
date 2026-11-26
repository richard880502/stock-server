"use client";

import { Sparkline } from "@/components/charts/sparkline";
import { actionCopy, actionTone } from "@/lib/copy";
import { useDashboard } from "@/lib/dashboard-context";
import { directionOf, formatPrice, formatSignedPct } from "@/lib/format";
import type { Signal } from "@/lib/api/types";
import { useNow } from "./rail-now";
import { useRailSignals } from "./rail-signals";
import { isStaleQuote } from "./rail-stale";
import type { LiveState, RailItem } from "./types";
import "./rail.css";

/** Compact watchlist: live price (else daily close), change, trend line and our signal. */
export function WatchRail({
  items,
  signedIn,
  live,
  selected,
  onSelect,
}: {
  items: RailItem[];
  signedIn: boolean;
  live: LiveState;
  selected: string;
  onSelect: (symbol: string) => void;
}) {
  const { query } = useDashboard();
  const now = useNow(5000);
  const signals = useRailSignals(
    items.map((item) => item.symbol),
    query.asOf,
    query.benchmark,
  );
  return (
    <aside className="terminal-rail rail-panel" aria-label="自選清單">
      <header className="rail-head">
        <h2 className="rail-title">自選清單</h2>
        <span className="rail-count">{items.length || ""}</span>
      </header>
      {!signedIn && <p className="rail-note">登入 Google 帳號後可在此看到自選股{items.length ? "；目前顯示最近查看的股票。" : "。"}</p>}
      {items.length === 0 ? (
        <p className="rail-empty">還沒有股票。按 ⌘K（或 Ctrl+K）搜尋並加入自選。</p>
      ) : (
        <ul className="rail-list">
          {items.map((item) => (
            <li key={item.symbol}>
              <RailRow
                item={item}
                live={live}
                now={now?.getTime() ?? null}
                signal={signals.get(item.symbol)}
                active={item.symbol === selected}
                onSelect={onSelect}
              />
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}

function RailRow({
  item,
  live,
  now,
  signal,
  active,
  onSelect,
}: {
  item: RailItem;
  live: LiveState;
  now: number | null;
  signal: Signal | undefined;
  active: boolean;
  onSelect: (symbol: string) => void;
}) {
  const quote = live.quotes.get(item.symbol);
  const price = quote?.price ?? item.close;
  const change = quote ? quote.change_pct : item.dayChangePct;
  const stale = !!quote && now !== null && isStaleQuote(quote, live.status, now);
  const tone = change === null ? "flat" : directionOf(change);
  const closes = item.recentCloses;
  const spark = quote && closes.length ? [...closes, quote.price] : closes;
  return (
    <button
      type="button"
      className="rail-row"
      aria-pressed={active}
      data-stale={stale || undefined}
      data-live={quote ? "true" : undefined}
      onClick={() => onSelect(item.symbol)}
    >
      <span className="rail-id">
        <span className="rail-code">{item.symbol.replace(/\.TWO?$/, "")}</span>
        <span className="rail-name">{item.name}</span>
      </span>
      <span className={`rail-price text-${tone === "flat" ? "muted" : tone}`}>
        {price === null ? "—" : formatPrice(price)}
        {stale && <span className="rail-stale" title="報價可能已過時" aria-label="報價可能已過時" />}
      </span>
      <span className="rail-sub">
        {signal ? (
          <span className={`rail-badge tone-${actionTone[signal.action]}`} title={`${actionCopy[signal.action].label}，評分 ${signal.score.toFixed(0)}（${signal.as_of}）`}>
            {actionCopy[signal.action].label} {signal.score.toFixed(0)}
          </span>
        ) : (
          <span className="rail-badge-empty" aria-hidden="true" />
        )}
      </span>
      <span className="rail-spark">
        <Sparkline values={spark} width={56} height={18} label={`${item.symbol} 近期走勢`} />
      </span>
      <span className={`rail-change text-${tone === "flat" ? "muted" : tone}`}>{change === null ? "—" : formatSignedPct(change, 2)}</span>
    </button>
  );
}
