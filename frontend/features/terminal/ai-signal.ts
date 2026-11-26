import type { Alert, ChipFlow, MarketEnvironment, Signal } from "@/lib/api/types";
import { factorLabels } from "@/lib/copy";

export type FactorRow = { key: keyof Signal["factors"]; label: string; value: number };

/** The `count` highest-scoring factors, strongest first (ties keep declaration order). */
export function topFactors(factors: Signal["factors"], count = 3): FactorRow[] {
  return (Object.entries(factors) as [keyof Signal["factors"], number][])
    .map(([key, value], index) => ({ key, label: factorLabels[key], value, index }))
    .sort((a, b) => b.value - a.value || a.index - b.index)
    .slice(0, count)
    .map(({ key, label, value }) => ({ key, label, value }));
}

export type SourcedAlert = Alert & { source: "symbol" | "chip" | "market" };

const severityRank: Record<Alert["severity"], number> = { high: 0, medium: 1, info: 2 };

/** Symbol, chip and market alerts in one list, most severe first. */
export function collectAlerts(
  signal: Pick<Signal, "alerts"> | null,
  chipFlow: Pick<ChipFlow, "alerts"> | null,
  market: Pick<MarketEnvironment, "alerts"> | null,
): SourcedAlert[] {
  const all: SourcedAlert[] = [
    ...(signal?.alerts ?? []).map((a) => ({ ...a, source: "symbol" as const })),
    ...(chipFlow?.alerts ?? []).map((a) => ({ ...a, source: "chip" as const })),
    ...(market?.alerts ?? []).map((a) => ({ ...a, source: "market" as const })),
  ];
  return all
    .map((alert, index) => ({ alert, index }))
    .sort((a, b) => severityRank[a.alert.severity] - severityRank[b.alert.severity] || a.index - b.index)
    .map(({ alert }) => alert);
}
