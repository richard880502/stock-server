"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ReactNode, useEffect, useMemo } from "react";
import { EmptyState } from "@/components/empty-state";
import { SearchIcon } from "@/components/icons";
import { WatchlistStar } from "@/components/watchlist-star";
import { ChipPanel } from "@/features/signal/chip-panel";
import { AlertsCard, FactorsCard, type SourcedAlert } from "@/features/signal/evidence-cards";
import { VerdictHero } from "@/features/signal/verdict-hero";
import { paperLink } from "@/features/paper/paper-math";
import { AiRail } from "@/features/terminal/ai-rail";
import { useNow } from "@/features/terminal/rail-now";
import { useSession } from "@/lib/api/session";
import { useDashboard } from "@/lib/dashboard-context";
import { useLiveQuotes } from "@/lib/live-quotes";
import { searchInstruments } from "@/lib/api/endpoints";
import { backtestHref, stockHref } from "@/lib/stock-routes";
import { isEtfSymbol } from "@/lib/symbols";
import { PriceChartCard } from "./price-chart-card";
import { profileAsOf } from "./profile/model";
import { StockProfileSection } from "./profile/profile-section";
import { QuickStockPanel } from "./quick-stock-panel";
import { pickHeaderPrice, stockTitle } from "./page/model";
import { SectionNav } from "./page/section-nav";
import { EvidenceSkeleton, VerdictPlaceholder } from "./page/signal-skeleton";
import { StockHeader } from "./page/stock-header";
import { useActiveSection, useHashScroll } from "./page/use-sections";
import { useStockOverview } from "./use-stock-overview";

const NO_SYMBOLS: string[] = [];

/**
 * `/stock/6488` has no market suffix, so it cannot say 上市 or 上櫃. Look the code up and replace
 * the URL with the full symbol; when the lookup fails, fall back to the listed market.
 */
function BareCodeRedirect({ code }: { code: string }) {
  const router = useRouter();
  useEffect(() => {
    const controller = new AbortController();
    const go = (symbol: string) => router.replace(stockHref(symbol) + window.location.hash);
    searchInstruments(code, { signal: controller.signal })
      .then((rows) => go(rows.find((row) => row.symbol.split(".")[0] === code)?.symbol ?? `${code}.TW`))
      .catch(() => {
        if (!controller.signal.aborted) go(`${code}.TW`);
      });
    return () => controller.abort();
  }, [code, router]);
  return (
    <p className="note" role="status">
      正在開啟 {code}…
    </p>
  );
}

/**
 * One stock's page at `/stock/<symbol>`. `symbol` is null when the path is not a full symbol;
 * `bareCode` is then set if it is a code without its market suffix.
 */
export function StockPage({ symbol, bareCode = null }: { symbol: string | null; bareCode?: string | null }) {
  if (!symbol && bareCode) return <BareCodeRedirect code={bareCode} />;
  if (!symbol) {
    return (
      <EmptyState
        icon={<SearchIcon size={22} />}
        title="查無此股票代號"
        action={
          <Link href="/screener" className="button primary">
            前往選股
          </Link>
        }
      >
        請確認網址中的代號（例如 2330.TW 或 6488.TWO）。按 ⌘K 可以搜尋股票。
      </EmptyState>
    );
  }
  // Keyed by symbol so the live stream, fallback price and tab state never carry over to the next stock.
  return <StockPageBody key={symbol} symbol={symbol} />;
}

function Cell({ id, className = "", children }: { id?: string; className?: string; children: ReactNode }) {
  return (
    <div id={id} className={`stock-sec ${className}`.trim()}>
      {children}
    </div>
  );
}

function StockPageBody({ symbol }: { symbol: string }) {
  const { query, signalData, error, refresh, instrumentName } = useDashboard();
  const { config } = useSession();
  const { signal: loadedSignal, marketEnvironment, chipFlow, loading, syncWait, cancelSync } = signalData;
  // The provider syncs query.symbol one effect after the route changes: compare with the page's symbol.
  const signal = loadedSignal?.symbol === symbol ? loadedSignal : null;
  const synced = query.symbol === symbol;
  const refreshing = Boolean(signal) && loading;

  // Exactly one live stream for this page; the chart and the AI rail share its quote.
  const liveEnabled = config?.quotes === true;
  const symbols = useMemo(() => [symbol], [symbol]);
  const stream = useLiveQuotes(liveEnabled ? symbols : NO_SYMBOLS);
  const quote = liveEnabled ? stream.quotes.get(symbol.toUpperCase()) : undefined;
  const overview = useStockOverview(symbol, 0);
  // The instrument name is resolved once the signal has loaded; until then (or when the full
  // analysis is not ready) the snapshot carries it.
  const overviewName = overview.data?.symbol === symbol ? overview.data.name || null : null;
  const name = (instrumentName?.symbol === symbol ? instrumentName.name || null : null) ?? overviewName;
  useEffect(() => {
    if (name) document.title = stockTitle(symbol, name);
  }, [symbol, name]);

  const now = useNow(5000);
  const price = pickHeaderPrice({
    quote,
    status: stream.status,
    now: now?.getTime() ?? null,
    overview: overview.data,
    symbol,
  });

  const ready = Boolean(signal) && synced;
  const activeSection = useActiveSection(ready);
  useHashScroll(ready, symbol);

  const rail = (
    <div className="stock-rail">
      <AiRail symbol={symbol} name={name} quote={quote} showSignal={false} variant="page" />
    </div>
  );

  const etf = isEtfSymbol(symbol);
  let body: ReactNode;
  if (etf) {
    // ETFs have official daily quotes only: no signal, filings or chip data exist for them.
    body = (
      <div className="stock-grid">
        <Cell id="summary" className="stock-main">
          <QuickStockPanel
            symbol={symbol}
            syncWait={null}
            signalLoading={false}
            onCancelSync={cancelSync}
            onReload={() => refresh()}
          />
        </Cell>
        {rail}
        <Cell id="profile" className="stock-main">
          <EmptyState icon={<SearchIcon size={22} />} title="個股全貌">
            ETF 不適用個股財報與籌碼分析。
          </EmptyState>
        </Cell>
      </div>
    );
  } else if (!signal || !synced) {
    if (synced && (syncWait || (error && !loading))) {
      // No full signal yet (history still being prepared, too short, or the wait ran out):
      // the instant snapshot view with the status of the full analysis.
      body = (
        <div className="stock-grid">
          <Cell id="summary" className="stock-main">
            <QuickStockPanel
              symbol={symbol}
              syncWait={syncWait}
              signalLoading={loading}
              onCancelSync={cancelSync}
              onReload={() => refresh()}
            />
          </Cell>
          {rail}
        </div>
      );
    } else {
      body = (
        <div className="stock-grid" aria-busy="true">
          <Cell id="summary" className="stock-main">
            <VerdictPlaceholder />
          </Cell>
          {rail}
          <Cell className="stock-main">
            <EvidenceSkeleton />
          </Cell>
        </div>
      );
    }
  } else {
    const alerts: SourcedAlert[] = [
      ...(signal.alerts ?? []).map((a) => ({ ...a, source: "symbol" as const })),
      ...(chipFlow?.alerts ?? []).map((a) => ({ ...a, source: "chip" as const })),
      ...(marketEnvironment?.alerts ?? []).map((a) => ({ ...a, source: "market" as const })),
    ];
    const dim = refreshing ? " is-refreshing" : "";
    body = (
      <div className="stock-grid">
        <Cell id="summary" className="stock-main">
          <VerdictHero signal={signal} name={name} alertCount={alerts.length} refreshing={refreshing} showIdentity={false} />
        </Cell>
        {rail}
        <Cell id="chart" className={`stock-main${dim}`}>
          <PriceChartCard
            symbol={signal.symbol}
            name={name}
            asOf={signal.as_of}
            liveAsOf={!refreshing ? query.asOf : undefined}
            liveFeed={{ quote, status: stream.status }}
          />
        </Cell>
        <Cell id="evidence" className={`stock-main evidence${dim}`}>
          <div className="section-title">
            <h2>支持這個結論的證據</h2>
            <p>因子、異常與市場背景；展開細節可查看原始欄位。</p>
          </div>
          <div className="grid">
            <div className="span-5">
              <FactorsCard signal={signal} />
            </div>
            <div className="span-7">
              <AlertsCard alerts={alerts} />
            </div>
          </div>
        </Cell>
        <Cell id="profile" className="stock-main">
          <StockProfileSection symbol={signal.symbol} asOf={profileAsOf(query.asOf, signal.as_of)} />
        </Cell>
        <Cell id="chips" className={`stock-main${dim}`}>
          <ChipPanel chipFlow={chipFlow} />
        </Cell>
        <Cell className="stock-main">
          <div className="stock-next">
            <h2>下一步</h2>
            <div className="stock-next-actions">
              <Link href={backtestHref(symbol)} className="button primary">
                回測這檔
              </Link>
              <Link href={paperLink(symbol, "buy")} className="button">
                模擬買進
              </Link>
              <WatchlistStar symbol={symbol} />
            </div>
          </div>
        </Cell>
      </div>
    );
  }

  return (
    <div className="stock-page">
      <StockHeader symbol={symbol} name={name} price={price} liveEnabled={liveEnabled} />
      <SectionNav active={activeSection} etf={etf} />
      {body}
    </div>
  );
}
