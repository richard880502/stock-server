import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { DebateReport } from "@/lib/api/types";

const dashboard = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));
vi.mock("@/lib/dashboard-context", () => ({ useDashboard: () => dashboard.current }));

import { AiRail } from "./ai-rail";

const REPORT = {
  symbol: "2330.TW",
  model: "m",
  judge_model: "m",
  prompt_version: "debate_v4",
  as_of: "2026-11-27",
  data_sufficient: true,
  bull_case: { stance: "bullish", thesis: "多方", key_evidence: [], counterpoints_to_address: [] },
  bear_case: { stance: "bearish", thesis: "空方", key_evidence: [], counterpoints_to_address: [] },
  judgement: {
    headline: "台積電專屬裁決",
    stance: "cautious",
    operation_guide: "台積電專屬操作",
    key_evidence: [],
    risk_warnings: [],
    scenarios: [],
    limitations: [],
  },
  risk_review: null,
} as unknown as DebateReport;

/** The shared LLM state holds finished results for `querySymbol`. */
function setDashboard(querySymbol: string) {
  dashboard.current = {
    query: { symbol: querySymbol, asOf: "2026-11-27", benchmark: "^TWII", market: "TW" },
    llmStatus: { configured: true, model: "m" },
    signalData: { signal: null, marketEnvironment: null, chipFlow: null, loading: false, syncWait: null },
    llm: {
      streaming: false,
      streamedGuide: "台積電專屬判讀",
      streamModel: "m",
      streamError: null,
      streamSyncWait: null,
      debating: false,
      debateReport: REPORT,
      debateError: null,
      debateSyncWait: null,
      runStream: () => {},
      runDebate: () => {},
      cancelStreamSync: () => {},
      cancelDebateSync: () => {},
    },
  };
}

const html = (symbol: string) =>
  renderToStaticMarkup(<AiRail symbol={symbol} name={null} showSignal={false} variant="page" />);

describe("AiRail and the shared LLM state", () => {
  it("shows the results on the rail of the stock they were generated for", () => {
    setDashboard("2330.TW");
    const out = html("2330.TW");
    expect(out).toContain("台積電專屬判讀");
    expect(out).toContain("台積電專屬裁決");
  });

  it("never shows them on another stock's rail while the query has not switched yet", () => {
    // /stock/2317.TW has just mounted; the provider still holds 2330's query and results.
    setDashboard("2330.TW");
    const out = html("2317.TW");
    expect(out).not.toContain("台積電專屬判讀");
    expect(out).not.toContain("台積電專屬裁決");
    expect(out).not.toContain("台積電專屬操作");
  });
});
