import { describe, expect, it } from "vitest";
import { META } from "@/features/screener/fixtures";
import { FACTORS_FULL, HOLDERS_FULL, HOLDERS_PARTIAL, SHORT_FULL, SHORT_OTC } from "./fixtures";
import {
  dayTradeReason,
  daysUntil,
  daysUntilText,
  emptyReason,
  formatYiFromThousands,
  holdersHistoryNote,
  marketCap,
  monthLabel,
  nextTab,
  parseTabHash,
  percentileReason,
  percentileText,
  PROFILE_TABS,
  rankText,
  recentAverage,
  scoreBars,
  stockDividendText,
  tabHash,
  thousandsToYi,
  toneOf,
  weeklyChange,
} from "./model";

describe("units", () => {
  it("converts NT$ thousands to 億 (1 億 = 100,000 千元)", () => {
    expect(thousandsToYi(586_890_319)).toBeCloseTo(5868.90319, 5);
    expect(formatYiFromThousands(586_890_319)).toBe("5,868.9 億");
    expect(formatYiFromThousands(100_000)).toBe("1.0 億");
    expect(formatYiFromThousands(null)).toBe("—");
  });
  it("multiplies shares by close for market cap and tolerates missing inputs", () => {
    expect(marketCap(1000, 2.5)).toBe(2500);
    expect(marketCap(null, 2)).toBeNull();
    expect(marketCap(1000, null)).toBeNull();
  });
  it("uses the Taiwan tone convention and a compact month label", () => {
    expect(toneOf(1)).toBe("text-up");
    expect(toneOf(-1)).toBe("text-down");
    expect(toneOf(0)).toBe("");
    expect(toneOf(null)).toBe("");
    expect(monthLabel("2026-07")).toBe("26/07");
  });
});

describe("percentile wording", () => {
  it("rounds and clamps", () => {
    expect(percentileText(23.4)).toBe("位於近一年 23% 分位");
    expect(percentileText(0)).toBe("位於近一年 0% 分位");
    expect(percentileText(130)).toBe("位於近一年 100% 分位");
    expect(percentileText(null)).toBeNull();
  });
  it("says why a percentile is missing", () => {
    expect(percentileReason(22, null)).toContain("60 筆");
    expect(percentileReason(null, null)).toContain("沒有數值");
    expect(percentileReason(22, 50)).toBeNull();
  });
});

describe("daysUntil", () => {
  it("counts calendar days across month and year ends", () => {
    expect(daysUntil("2026-12-10", "2026-11-27")).toBe(13);
    expect(daysUntil("2027-01-05", "2026-12-31")).toBe(5);
    expect(daysUntil("2026-11-27", "2026-11-27")).toBe(0);
    expect(daysUntil("2026-11-20", "2026-11-27")).toBe(-7);
  });
  it("words it", () => {
    expect(daysUntilText(13)).toBe("13 天後");
    expect(daysUntilText(0)).toBe("今天");
    expect(daysUntilText(-2)).toBe("2 天前");
  });
  it("describes stock dividends per thousand shares", () => {
    expect(stockDividendText(0.05)).toBe("每千股配 50 股");
    expect(stockDividendText(0)).toBe("—");
    expect(stockDividendText(null)).toBe("—");
  });
});

describe("empty-state reasons", () => {
  it("prefers a server explanation, falls back to the built-in line", () => {
    expect(emptyReason("futures")).toContain("沒有個股期貨");
    expect(emptyReason("factors", "快照尚未產生")).toBe("快照尚未產生");
    expect(emptyReason("factors", "  ")).toContain("選股快照");
    expect(emptyReason("events")).toBe("未來 60 天沒有已公告的除權息。");
  });
  it("explains missing day-trade data differently for OTC stocks", () => {
    expect(dayTradeReason("6488.TWO", SHORT_OTC.days)).toContain("上櫃");
    expect(dayTradeReason("2330.TW", SHORT_OTC.days)).toContain("尚未取得");
    expect(dayTradeReason("2330.TW", SHORT_FULL.days)).toBeNull();
  });
  it("flags a short holder history but not a long one", () => {
    expect(holdersHistoryNote(HOLDERS_PARTIAL)).toContain("1 週");
    expect(holdersHistoryNote({ ...HOLDERS_FULL, series: HOLDERS_FULL.series.slice(0, 3) })).toContain("3 週");
    expect(holdersHistoryNote(HOLDERS_FULL)).toBeNull();
    expect(holdersHistoryNote({ ...HOLDERS_FULL, series: [] })).toBeNull();
  });
});

describe("series helpers", () => {
  it("computes the weekly change only with two weeks", () => {
    expect(weeklyChange(HOLDERS_PARTIAL.series, (p) => p.pct_ge400)).toBeNull();
    expect(weeklyChange(HOLDERS_FULL.series, (p) => p.pct_ge400)).toBeCloseTo(0.2127, 4);
  });
  it("averages the last n available day-trade ratios", () => {
    const days = [10, null, 20, 30].map((v, i) => ({ date: String(i), sbl_balance_lots: 1, sbl_sold_lots: 1, sbl_returned_lots: 1, margin_short_balance_lots: 1, day_trade_ratio_pct: v }));
    expect(recentAverage(days, 2)).toBe(25);
    expect(recentAverage(days, 10)).toBe(20);
    expect(recentAverage([], 5)).toBeNull();
  });
});

describe("tabs", () => {
  it("round-trips the hash and rejects unknown values", () => {
    for (const tab of PROFILE_TABS) expect(parseTabHash(tabHash(tab.id))).toBe(tab.id);
    expect(parseTabHash("#profile=nope")).toBeNull();
    expect(parseTabHash("#stock-signal")).toBeNull();
    expect(parseTabHash("")).toBeNull();
  });
  it("moves with arrows, wraps, and jumps with Home / End", () => {
    expect(nextTab("valuation", "ArrowRight")).toBe("earnings");
    expect(nextTab("valuation", "ArrowLeft")).toBe("events");
    expect(nextTab("events", "ArrowRight")).toBe("valuation");
    expect(nextTab("holders", "Home")).toBe("valuation");
    expect(nextTab("holders", "End")).toBe("events");
    expect(nextTab("holders", "a")).toBeNull();
  });
});

describe("key facts", () => {
  it("words the rank, with and without a universe", () => {
    expect(rankText(312, 1947)).toBe("綜合排名 312 / 1,947");
    expect(rankText(5, 0)).toBe("綜合排名 5");
    expect(rankText(null, 100)).toBeNull();
  });
  it("labels scores from the catalogue, in catalogue order, dropping nulls", () => {
    const bars = scoreBars(FACTORS_FULL.entry!, META);
    expect(bars.map((b) => b.label)).toEqual(["價格動能", "資金流向", "基本面", "穩定度"]);
    expect(bars[0].value).toBe(82.4);
  });
  it("falls back to raw keys when the catalogue is missing", () => {
    expect(scoreBars(FACTORS_FULL.entry!, null).map((b) => b.key).sort()).toEqual(["flow", "fundamental", "momentum", "stability"]);
  });
});
