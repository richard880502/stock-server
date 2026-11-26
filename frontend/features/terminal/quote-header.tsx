"use client";

import { Direction } from "@/components/direction";
import type { Signal } from "@/lib/api/types";
import { actionCopy, actionTone } from "@/lib/copy";
import { directionOf, formatLots, formatPrice, formatSigned, formatSignedPct } from "@/lib/format";
import type { LiveQuote } from "@/lib/live-quotes";
import { useNow } from "./rail-now";
import { taipeiClock } from "./rail-session";
import { isStaleQuote, referencePrice } from "./rail-stale";
import type { LiveState } from "./types";
import "./rail.css";

const dayStat = (value: number | null | undefined) => (typeof value === "number" && value > 0 ? formatPrice(value) : "—");

/** Big price, change, reference price, volume and day range for the selected stock. */
export function QuoteHeader({
  symbol,
  name,
  quote,
  signal,
  live,
}: {
  symbol: string;
  name: string | null;
  quote?: LiveQuote;
  signal: Signal | null;
  live: LiveState;
}) {
  const now = useNow(5000);
  const reference = quote ? referencePrice(quote.price, quote.change_pct) : null;
  const change = quote && reference !== null ? quote.price - reference : null;
  const tone = quote?.change_pct == null ? "flat" : directionOf(quote.change_pct);
  const stale = !!quote && now !== null && isStaleQuote(quote, live.status, now.getTime());
  const tradeTime = quote ? new Date(quote.trade_time) : null;
  return (
    <section className="rail-panel quote-header" aria-label="即時報價">
      <header className="quote-title">
        <h2 className="quote-name">
          <span className="quote-code">{symbol.replace(/\.TWO?$/, "")}</span>
          {name && <span className="quote-label">{name}</span>}
        </h2>
        {signal && (
          <span className={`rail-badge tone-${actionTone[signal.action]}`} title={`確定性訊號（${signal.as_of}）`}>
            {actionCopy[signal.action].label} {signal.score.toFixed(0)}
          </span>
        )}
      </header>
      {quote ? (
        <div className="quote-body" data-stale={stale || undefined}>
          <div className={`quote-price text-${tone === "flat" ? "muted" : tone}`}>{formatPrice(quote.price)}</div>
          <div className="quote-change">
            {quote.change_pct !== null && (
              <Direction value={quote.change_pct}>
                {change !== null && `${formatSigned(change, 2)}　`}
                {formatSignedPct(quote.change_pct, 2)}
              </Direction>
            )}
          </div>
          <dl className="quote-stats">
            <Stat label="參考價" value={reference === null ? "—" : formatPrice(reference)} />
            <Stat label="開" value={dayStat(quote.day_open)} />
            <Stat label="高" value={dayStat(quote.day_high)} tone="up" />
            <Stat label="低" value={dayStat(quote.day_low)} tone="down" />
            <Stat label="總量(張)" value={quote.total_volume === null ? "—" : formatLots(quote.total_volume)} />
            <Stat
              label="最後成交"
              value={tradeTime && Number.isFinite(tradeTime.getTime()) ? taipeiClock(tradeTime) : "—"}
              note={stale ? "已過時" : undefined}
            />
          </dl>
        </div>
      ) : (
        <p className="quote-empty">
          尚無即時成交
          {signal && <span className="quote-asof">　資料截至 {signal.as_of}</span>}
          {!live.enabled && <span className="quote-asof">　（即時報價未啟用）</span>}
        </p>
      )}
    </section>
  );
}

function Stat({ label, value, tone, note }: { label: string; value: string; tone?: "up" | "down"; note?: string }) {
  return (
    <div className="quote-stat">
      <dt>{label}</dt>
      <dd className={tone && value !== "—" ? `text-${tone}` : undefined}>
        {value}
        {note && <span className="quote-note"> {note}</span>}
      </dd>
    </div>
  );
}
