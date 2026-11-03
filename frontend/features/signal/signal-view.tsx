"use client";

import { useDashboard } from "@/lib/dashboard-context";
import { LlmGuidancePanel } from "@/features/llm/llm-guidance-panel";
import { DebatePanel } from "@/features/llm/debate-panel";
import { ChipPanel } from "./chip-panel";
import { MarketPanel } from "./market-panel";
import { AlertsCard, FactorsCard, ScoreCard } from "./signal-cards";
import type { Alert } from "@/lib/api/types";

export function SignalView() {
  const { query, signalData } = useDashboard();
  const { signal, marketEnvironment, chipFlow } = signalData;

  const combinedAlerts: Alert[] = [
    ...(marketEnvironment?.alerts ?? []).map((alert) => ({
      ...alert,
      code: `market:${alert.code}`,
      title: `市場｜${alert.title}`,
    })),
    ...(signal?.alerts ?? []).map((alert) => ({
      ...alert,
      code: `symbol:${alert.code}`,
      title: `個股｜${alert.title}`,
    })),
    ...(chipFlow?.alerts ?? []).map((alert) => ({
      ...alert,
      code: `chip:${alert.code}`,
      title: `籌碼｜${alert.title}`,
    })),
  ];

  return (
    <>
      <MarketPanel market={query.market} environment={marketEnvironment} />
      <section className="signal-grid" aria-label="個股訊號">
        <ScoreCard signal={signal} />
        <FactorsCard signal={signal} />
        <AlertsCard alerts={combinedAlerts} />
      </section>
      <ChipPanel chipFlow={chipFlow} />
      <LlmGuidancePanel />
      <DebatePanel />
    </>
  );
}
