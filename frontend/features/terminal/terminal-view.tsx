"use client";

import { type ReactNode, useEffect, useMemo } from "react";
import { PriceChartCard } from "@/features/stock/price-chart-card";
import { useSession } from "@/lib/api/session";
import { useDashboard } from "@/lib/dashboard-context";
import { today } from "@/lib/format";
import { useLiveQuotes } from "@/lib/live-quotes";
import { useWatchlistAdd } from "@/features/watchlist/use-watchlist-add";
import { useWatchlist } from "@/lib/use-watchlist";
import { AiRail } from "./ai-rail";
import { QuoteHeader } from "./quote-header";
import { TopStrip } from "./top-strip";
import type { LiveState, RailItem } from "./types";
import { WatchRail } from "./watch-rail";
import "./terminal.css";

const NO_SYMBOLS: string[] = [];

/**
 * 看盤: a dense, single-screen view. Left: watchlist with live prices. Centre: the
 * selected stock's quote and live/daily K-line. Right: our deterministic signal and
 * on-demand AI suggestions. No order entry: this app does not trade.
 *
 * The selected stock is the dashboard's shared symbol, so switching here also
 * switches the other pages (and the other pages' choice opens here).
 */
export function TerminalView({ switcher }: { switcher?: ReactNode } = {}) {
  const { query, refresh, signalData, instrumentName, recentSymbols } = useDashboard();
  const { watchlist, phase, count, limit, pending, setMember } = useWatchlist();
  const { add, note } = useWatchlistAdd(setMember);
  const { config } = useSession();
  const symbol = query.symbol;

  // The terminal is about today; reload if another page left a past as-of date.
  const asOf = query.asOf;
  useEffect(() => {
    const now = today();
    if (asOf !== now) refresh({ asOf: now });
    // Only on entering the page: later as-of changes are the viewer's choice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const railItems = useMemo<RailItem[]>(() => {
    const items = watchlist?.items ?? [];
    if (items.length)
      return items.map((item) => ({
        symbol: item.symbol.toUpperCase(),
        name: item.name,
        close: item.close,
        // The watchlist API sends percent (-3.59); live quotes send a ratio (-0.0359).
        dayChangePct: item.day_change_pct === null ? null : item.day_change_pct / 100,
        recentCloses: item.recent_closes ?? [],
      }));
    return recentSymbols.map((recent) => ({
      symbol: recent.symbol.toUpperCase(),
      name: recent.name ?? "",
      close: null,
      dayChangePct: null,
      recentCloses: [],
    }));
  }, [watchlist, recentSymbols]);

  const liveEnabled = config?.quotes === true;
  const streamSymbols = useMemo(
    () => [...new Set([symbol.toUpperCase(), ...railItems.map((item) => item.symbol)])].slice(0, 25),
    [symbol, railItems],
  );
  const stream = useLiveQuotes(liveEnabled ? streamSymbols : NO_SYMBOLS);
  const live: LiveState = { enabled: liveEnabled, status: stream.status, quotes: stream.quotes };
  const quote = live.quotes.get(symbol.toUpperCase());
  const name = instrumentName?.symbol === symbol ? instrumentName.name : null;
  const signal = signalData.signal?.symbol === symbol ? signalData.signal : null;

  return (
    <div className="terminal">
      <div className="terminal-head">
        <h1>自選股</h1>
        {switcher}
      </div>
      <TopStrip live={live} />
      <div className="terminal-grid">
        <WatchRail
          items={railItems}
          signedIn={!!watchlist}
          live={live}
          selected={symbol.toUpperCase()}
          onSelect={(next) => refresh({ symbol: next })}
          edit={{
            onAdd: add,
            onRemove: (symbol) => setMember(symbol, false),
            note,
            pending,
            members: new Set((watchlist?.items ?? []).map((item) => item.symbol.toUpperCase())),
            full: phase === "ready" && count >= limit,
            limit,
            loading: phase === "loading",
          }}
        />
        <section className="terminal-center" aria-label="報價與 K 線">
          <QuoteHeader symbol={symbol} name={name} quote={quote} signal={signal} live={live} />
          <PriceChartCard
            liveFeed={{ quote, status: live.status }}
            symbol={symbol}
            name={name}
            asOf={signal?.as_of}
            liveAsOf={signalData.loading ? undefined : query.asOf}
            preferLive
          />
        </section>
        <AiRail symbol={symbol} name={name} quote={quote} />
      </div>
    </div>
  );
}
