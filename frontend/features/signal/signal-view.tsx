"use client";

import { Card } from "@/components/card";
import { EmptyState } from "@/components/empty-state";
import { AlertIcon, ClockIcon } from "@/components/icons";
import { LlmSection } from "@/features/llm/llm-section";
import { useDashboard } from "@/lib/dashboard-context";
import { ChipPanel } from "./chip-panel";
import { AlertsCard, FactorsCard, type SourcedAlert } from "./evidence-cards";
import { MarketPanel } from "./market-panel";
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
  const { query, signalData, error, refresh, instrumentName } = useDashboard();
  const { signal, marketEnvironment, chipFlow, loading, syncWait } = signalData;

  if (!signal) {
    if (syncWait) {
      return (
        <EmptyState icon={<ClockIcon size={22} />} title={`正在準備 ${query.symbol} 的資料`} live>
          這個標的的歷史資料還不在資料庫，背景程序正在向交易所抓取。完成後會自動載入訊號，不需要重新整理。
        </EmptyState>
      );
    }
    if (error && !loading) {
      return (
        <EmptyState
          icon={<AlertIcon size={22} />}
          title={`目前沒有 ${query.symbol} 的訊號`}
          action={
            <button type="button" className="button" onClick={() => refresh()}>
              重新載入
            </button>
          }
        >
          詳細原因請見上方訊息。你也可以換一個標的或調整資料截止日。
        </EmptyState>
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
      <div className={`grid${refreshing ? " is-refreshing" : ""}`}>
        <div className="span-7">
          {marketEnvironment ? (
            <MarketPanel market={query.market} environment={marketEnvironment} asOf={query.asOf} />
          ) : (
            <Card title="市場環境" sub="尚無此市場的環境資料">
              <p className="note">取得市場寬度與資金流資料後，這裡會顯示大盤的風險環境。</p>
            </Card>
          )}
        </div>
        <div className="span-5">
          <ChipPanel chipFlow={chipFlow} />
        </div>
      </div>
      <LlmSection />
    </>
  );
}
