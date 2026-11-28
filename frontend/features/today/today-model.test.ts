import { describe, expect, it } from "vitest";
import type { AccountSummary } from "@/lib/api/paper";
import type { MarketDerivatives, MarketDerivativesDay } from "@/lib/api/profile";
import type { Alert, ScreenerEntry, ScreenerMeta, SectorRow, WatchlistItem } from "@/lib/api/types";
import {
  candidatePresets,
  derivativeHeadlines,
  extremeSectors,
  paperSummary,
  screenerHref,
  supportingMetric,
  topAlerts,
  watchRows,
} from "./today-model";

const sector = (code: string, name: string, equal: number | null): SectorRow =>
  ({ code, name, returns: { "5": { equal_pct: equal, weighted_pct: null } } }) as unknown as SectorRow;

describe("extremeSectors", () => {
  const rows = [
    sector("a", "甲", 1),
    sector("b", "乙", 5),
    sector("c", "丙", -2),
    sector("d", "丁", 3),
    sector("e", "戊", -4),
    sector("f", "己", null),
    sector("g", "庚", 0.5),
    sector("h", "辛", -1),
  ];
  it("ranks by the window's equal-weight return and skips missing values", () => {
    const { strongest, weakest } = extremeSectors(rows, 5, 3);
    expect(strongest.map((s) => s.name)).toEqual(["乙", "丁", "甲"]);
    expect(weakest.map((s) => s.name)).toEqual(["戊", "丙", "辛"]);
  });
  it("never lists a sector on both sides when there are few", () => {
    const { strongest, weakest } = extremeSectors(rows.slice(0, 3), 5, 3);
    const all = [...strongest, ...weakest].map((s) => s.code);
    expect(new Set(all).size).toBe(all.length);
    expect(strongest.length + weakest.length).toBe(3);
  });
  it("handles no data", () => {
    expect(extremeSectors(null, 5)).toEqual({ strongest: [], weakest: [] });
    expect(extremeSectors([sector("x", "x", null)], 5).strongest).toEqual([]);
  });
});

describe("derivativeHeadlines", () => {
  const day = (over: Partial<MarketDerivativesDay>): MarketDerivativesDay => ({
    date: "2026-11-27",
    foreign_tx_net_oi: null,
    foreign_tx_equiv_net_oi: null,
    foreign_net_oi_change: null,
    trust_tx_net_oi: null,
    dealer_tx_net_oi: null,
    pc_volume_ratio: null,
    pc_oi_ratio: null,
    foreign_call_net_oi: null,
    foreign_put_net_oi: null,
    top5_net_oi: null,
    top10_net_oi: null,
    vix: null,
    ...over,
  });
  const data = (days: MarketDerivativesDay[]) => ({ days }) as unknown as MarketDerivatives;

  it("formats the headline numbers with signs, tones and dates", () => {
    const [foreign, pc, vix] = derivativeHeadlines(
      data([day({ foreign_tx_equiv_net_oi: -106699.2, foreign_net_oi_change: -195.85, pc_oi_ratio: 46.67, vix: 51.94 })]),
    );
    expect(foreign.value).toBe("−106,699");
    expect(foreign.tone).toBe("down");
    expect(foreign.note).toContain("淨空單");
    expect(foreign.note).toContain("11/27");
    expect(pc.value).toBe("46.7%");
    expect(vix.value).toBe("51.94");
  });
  it("shows a dash, not 0, for missing fields and uses the newest day that has a value", () => {
    const items = derivativeHeadlines(
      data([day({ date: "2026-11-26", vix: 20 }), day({ date: "2026-11-27" })]),
    );
    expect(items[0].value).toBe("—");
    expect(items[0].note).toBe("尚無資料");
    expect(items[1].value).toBe("—");
    expect(items[2].value).toBe("20.00");
    expect(items[2].note).toBe("11/26");
  });
  it("survives a null payload", () => {
    expect(derivativeHeadlines(null).every((h) => h.value === "—")).toBe(true);
  });
});

describe("watchRows", () => {
  const item = (over: Partial<WatchlistItem>): WatchlistItem =>
    ({
      symbol: "2330.TW",
      name: "台積電",
      market: "TW",
      close: 1000,
      day_change_pct: -3.59,
      recent_closes: [1, 2],
      ...over,
    }) as WatchlistItem;
  it("maps close and percent change, capped at the limit", () => {
    const rows = watchRows(Array.from({ length: 12 }, (_, i) => item({ symbol: `${1000 + i}.TW` })), 8);
    expect(rows).toHaveLength(8);
    expect(rows[0]).toMatchObject({ code: "1000", price: 1000, changePct: -3.59, closes: [1, 2] });
  });
  it("prefers today's latest price and tolerates missing fields", () => {
    const [row] = watchRows([item({ latest_price: 1010, latest_change_pct: 1.2 })]);
    expect(row).toMatchObject({ price: 1010, changePct: 1.2 });
    const [bare] = watchRows([item({ close: null, day_change_pct: null, recent_closes: undefined })]);
    expect(bare).toMatchObject({ price: null, changePct: null, closes: [] });
    expect(watchRows(undefined)).toEqual([]);
  });
});

const meta = {
  factors: [{ key: "momentum", label: "動能", short: "動能", kind: "score", unit: "分", higher_is_better: true }],
  metrics: [
    { key: "ret_20d", label: "20 日報酬", short: "20日", unit: "%", higher_is_better: null },
    { key: "inst_flow_ratio_60d", label: "法人買超比", short: "法人比", unit: "%", higher_is_better: true },
  ],
  presets: [
    { key: "balanced", label: "綜合評分", description: "d1", sort: "composite", order: "desc", filters: [], columns: ["composite", "momentum"] },
    { key: "momentum", label: "強勢動能", description: "d2", sort: "momentum", order: "desc", filters: [], columns: ["momentum", "ret_20d"] },
    { key: "quiet_accumulation", label: "資金緩步流入", description: "d3", sort: "accumulation", order: "desc", filters: [], columns: ["accumulation", "inst_flow_ratio_60d"] },
  ],
} as unknown as ScreenerMeta;

describe("candidatePresets", () => {
  it("keeps the configured order and drops keys the server lacks", () => {
    expect(candidatePresets(meta).map((p) => p.key)).toEqual(["quiet_accumulation", "balanced", "momentum"]);
    const partial = { ...meta, presets: meta.presets.filter((p) => p.key !== "balanced") } as ScreenerMeta;
    expect(candidatePresets(partial).map((p) => p.key)).toEqual(["quiet_accumulation", "momentum"]);
    expect(candidatePresets(null)).toEqual([]);
  });
  it("builds the screener link with the preset parameter", () => {
    expect(screenerHref("momentum")).toBe("/screener?preset=momentum");
    expect(screenerHref("balanced")).toBe("/screener");
  });
});

describe("supportingMetric", () => {
  const entry = (over: Partial<ScreenerEntry>): ScreenerEntry =>
    ({ composite: 90, scores: { momentum: 94.1 }, metrics: { ret_20d: 2.68, inst_flow_ratio_60d: null }, ...over }) as ScreenerEntry;
  it("prefers a raw metric and keeps its sign tone", () => {
    expect(supportingMetric(entry({}), "momentum", meta)).toEqual({ label: "20日", text: "+2.7%", tone: "up" });
    expect(supportingMetric(entry({ metrics: { ret_20d: -1.5 } }), "momentum", meta)?.tone).toBe("down");
  });
  it("falls back to a factor score, then to null", () => {
    expect(supportingMetric(entry({}), "balanced", meta)).toMatchObject({ label: "動能", text: "94" });
    expect(supportingMetric(entry({}), "quiet_accumulation", meta)).toBeNull();
    expect(supportingMetric(entry({}), "unknown", null)).toBeNull();
  });
});

describe("topAlerts", () => {
  const alert = (code: string, severity: Alert["severity"]): Alert => ({ code, severity, title: code, evidence: {} });
  it("orders by severity and caps", () => {
    const env = { alerts: [alert("i", "info"), alert("m", "medium"), alert("h", "high")] };
    expect(topAlerts(env, 2).map((a) => a.code)).toEqual(["h", "m"]);
    expect(topAlerts(null)).toEqual([]);
    expect(topAlerts({ alerts: undefined as unknown as Alert[] })).toEqual([]);
  });
});

describe("paperSummary", () => {
  it("is null without an account and tolerates missing numbers", () => {
    expect(paperSummary([])).toBeNull();
    expect(paperSummary(undefined)).toBeNull();
    const s = paperSummary([{ id: "1", name: "主帳戶", equity: 1_000_000, positions_count: 2 } as AccountSummary]);
    expect(s).toMatchObject({ equity: 1_000_000, positions: 2, dayPnl: null, workingOrders: null, accountCount: 1 });
  });
});
