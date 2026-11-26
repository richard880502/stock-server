import { describe, expect, it } from "vitest";
import type { Alert } from "@/lib/api/types";
import { collectAlerts, topFactors } from "./ai-signal";

const factors = { trend: 70, momentum: 40, volume: 85, relative_strength: 55, risk_quality: 85 };
const alert = (code: string, severity: Alert["severity"]): Alert => ({ code, severity, title: code, evidence: {} });

describe("topFactors", () => {
  it("returns the three strongest, ties in declaration order", () => {
    expect(topFactors(factors).map((f) => f.key)).toEqual(["volume", "risk_quality", "trend"]);
  });
  it("labels factors in Chinese", () => {
    expect(topFactors(factors, 1)[0]).toMatchObject({ label: "量能確認", value: 85 });
  });
});

describe("collectAlerts", () => {
  it("merges sources and sorts by severity", () => {
    const merged = collectAlerts(
      { alerts: [alert("s-info", "info"), alert("s-high", "high")] },
      { alerts: [alert("c-med", "medium")] },
      { alerts: [alert("m-high", "high")] },
    );
    expect(merged.map((a) => `${a.source}:${a.code}`)).toEqual([
      "symbol:s-high",
      "market:m-high",
      "chip:c-med",
      "symbol:s-info",
    ]);
  });
  it("tolerates missing sources", () => {
    expect(collectAlerts(null, null, null)).toEqual([]);
  });
});
