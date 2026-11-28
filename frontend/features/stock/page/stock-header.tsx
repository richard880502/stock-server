"use client";

import Link from "next/link";
import { AsOfControl } from "@/components/as-of-control";
import { Direction } from "@/components/direction";
import { WatchlistStar } from "@/components/watchlist-star";
import { paperLink } from "@/features/paper/paper-math";
import { QuoteDayStats } from "@/features/terminal/quote-header";
import { taipeiClock } from "@/features/terminal/rail-session";
import { referencePrice } from "@/features/terminal/rail-stale";
import { directionOf, formatPrice, formatSigned, formatSignedPct } from "@/lib/format";
import { isEtfSymbol, marketOf } from "@/lib/symbols";
import { backtestHref, forecastHref } from "@/lib/stock-routes";
import { bareCode, type HeaderPrice } from "./model";

const MARKET_LABEL = { TW: "上市", TWO: "上櫃" } as const;

export function StockActions({ symbol }: { symbol: string }) {
  const etf = isEtfSymbol(symbol);
  return (
    <nav className="stock-actions" aria-label="個股操作">
      <Link href={paperLink(symbol, "buy")} className="button small primary" title="到模擬交易下單，不會送出真實委託">
        模擬買進
      </Link>
      {!etf && (
        <>
          <Link href={backtestHref(symbol)} className="button small">
            回測
          </Link>
          <Link href={forecastHref(symbol)} className="button small">
            情境預測
          </Link>
        </>
      )}
      <Link href="/watchlist?view=terminal" className="button small" title="在看盤工作區開啟即時報價與 K 線">
        在看盤開啟
      </Link>
    </nav>
  );
}

function tradeClock(value: string): string {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? taipeiClock(date) : "";
}

function PriceBlock({ price, liveEnabled }: { price: HeaderPrice | null; liveEnabled: boolean }) {
  if (!price) {
    return (
      <div className="stock-price" aria-label="報價">
        <span className="stock-price-main text-muted">—</span>
        <span className="stock-price-note">尚無價格資料{!liveEnabled && "（即時報價未啟用）"}</span>
      </div>
    );
  }
  const tone = price.changePct === null ? "muted" : directionOf(price.changePct) === "flat" ? "muted" : directionOf(price.changePct);
  const when =
    price.source === "live"
      ? `${tradeClock(price.asOfLabel)} 成交${price.stale ? " · 已過時" : ""}`
      : `${price.asOfLabel} 收盤${liveEnabled ? "" : " · 即時報價未啟用"}`;
  return (
    <div className="stock-price" data-stale={price.stale || undefined} aria-label="報價">
      <span className={`stock-price-main num text-${tone}`}>{formatPrice(price.price)}</span>
      {price.changePct !== null && (
        <Direction value={price.changePct} className="stock-price-change">
          {price.change !== null && `${formatSigned(price.change, 2)}　`}
          {formatSignedPct(price.changePct, 2)}
        </Direction>
      )}
      <span className="stock-price-note">{when}</span>
    </div>
  );
}

/** Code, name, market, price and the actions that lead on from this stock. */
export function StockHeader({
  symbol,
  name,
  price,
  liveEnabled,
}: {
  symbol: string;
  name: string | null;
  price: HeaderPrice | null;
  liveEnabled: boolean;
}) {
  const quote = price?.quote ?? null;
  const reference = quote ? referencePrice(quote.price, quote.change_pct) : null;
  return (
    <header className="stock-header">
      <div className="stock-header-top">
        <div className="stock-id">
          <h1 className="stock-title">
            <span className="stock-code mono">{bareCode(symbol)}</span>
            {name && <span className="stock-name">{name}</span>}
          </h1>
          <span className="chip plain">{MARKET_LABEL[marketOf(symbol)]}</span>
          {isEtfSymbol(symbol) ? (
            <span className="chip plain" title="交易所買賣基金：不適用個股財報與籌碼分析">
              ETF
            </span>
          ) : (
            <WatchlistStar symbol={symbol} variant="icon" />
          )}
        </div>
        <PriceBlock price={price} liveEnabled={liveEnabled} />
        <div className="stock-asof">
          <AsOfControl withBenchmark />
        </div>
      </div>
      <StockActions symbol={symbol} />
      {quote && (
        <div className="stock-daystats">
          <QuoteDayStats quote={quote} reference={reference} stale={Boolean(price?.stale)} />
        </div>
      )}
    </header>
  );
}
