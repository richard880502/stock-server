"use client";

import Link from "next/link";
import { Card } from "@/components/card";
import { LlmSection } from "@/features/llm/llm-section";
import { PriceChartCard } from "@/features/stock/price-chart-card";
import { profileAsOf } from "@/features/stock/profile/model";
import { StockProfileSection } from "@/features/stock/profile/profile-section";
import { QuickStockPanel } from "@/features/stock/quick-stock-panel";
import { useDashboard } from "@/lib/dashboard-context";
import { ChipPanel } from "./chip-panel";
import { AlertsCard, FactorsCard, type SourcedAlert } from "./evidence-cards";
import { MarketOverview } from "./market-overview";
import { VerdictHero, VerdictSkeleton } from "./verdict-hero";

function SignalSkeleton() {
  return (
    <>
      <VerdictSkeleton />
      <div className="grid">
        <section className="card span-5" aria-hidden="true">
          <span className="skeleton line" style={{ width: "35%" }} />
          {[0, 1, 2, 3, 4].map((i) => (
            <span key={i} className="skeleton line" style={{ marginTop: 18 }} />
          ))}
        </section>
        <section className="card span-7" aria-hidden="true">
          <span className="skeleton line" style={{ width: "30%" }} />
          {[0, 1, 2].map((i) => (
            <span key={i} className="skeleton block" style={{ height: 44, marginTop: 14 }} />
          ))}
        </section>
      </div>
    </>
  );
}

export function SignalView() {
  const { query } = useDashboard();
  return (
    <>
      {/* Phones: the stock verdict sits far below the market overview, so offer a jump to it. */}
      <a className="home-jump button" href="#stock-signal">
        ↓ 直接看 {query.symbol} 的訊號
      </a>
      <MarketOverview />
      <div id="stock-signal" className="scroll-target">
        <SignalDetail />
      </div>
    </>
  );
}

export function SignalDetail() {
  const { query, signalData, error, refresh, instrumentName } = useDashboard();
  const { signal, marketEnvironment, chipFlow, loading, syncWait, cancelSync } = signalData;

  if (!signal) {
    // No full signal yet (history still being prepared, too short, or the wait
    // ran out): show the instant snapshot view with the status of the full analysis.
    if (syncWait || (error && !loading)) {
      return (
        <QuickStockPanel
          symbol={query.symbol}
          syncWait={syncWait}
          signalLoading={loading}
          onCancelSync={cancelSync}
          onReload={() => refresh()}
        />
      );
    }
    return <SignalSkeleton />;
  }

  // A different symbol is on its way: don't present the old verdict under the new name.
  if (loading && signal.symbol !== query.symbol) return <SignalSkeleton />;
  const refreshing = loading;

  const alerts: SourcedAlert[] = [
    ...(signal.alerts ?? []).map((a) => ({ ...a, source: "symbol" as const })),
    ...(chipFlow?.alerts ?? []).map((a) => ({ ...a, source: "chip" as const })),
    ...(marketEnvironment?.alerts ?? []).map((a) => ({ ...a, source: "market" as const })),
  ];
  const name = instrumentName?.symbol === signal.symbol ? instrumentName.name || null : null;

  return (
    <>
      <VerdictHero
        signal={signal}
        name={name}
        alertCount={alerts.length}
        refreshing={refreshing}
      />
      <div className={refreshing ? "is-refreshing" : undefined}>
        <PriceChartCard symbol={signal.symbol} name={name} asOf={signal.as_of} liveAsOf={!refreshing && signal.symbol === query.symbol ? query.asOf : undefined} />
      </div>
      <div className={`evidence${refreshing ? " is-refreshing" : ""}`}>
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
      </div>
      <StockProfileSection symbol={signal.symbol} asOf={profileAsOf(query.asOf, signal.as_of)} />
      <div className={`grid${refreshing ? " is-refreshing" : ""}`}>
        <div className="span-12">
          <ChipPanel chipFlow={chipFlow} />
        </div>
      </div>
      <LlmSection />
      <Card title="用歷史資料檢驗策略" sub={`以 ${query.symbol}、截止 ${query.asOf} 的目前查詢設定回測。`}>
        <Link href="/backtest" className="button primary">前往策略回測</Link>
      </Card>
    </>
  );
}
