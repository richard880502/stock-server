import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/features/screener/use-screener-meta", async () => {
  const { META } = await import("@/features/screener/fixtures");
  return { useScreenerMeta: () => ({ meta: META, error: null, retry: () => {} }) };
});

import { DerivativesBody, DerivativesSection } from "@/features/market/derivatives-section";
import { derivativesEmptyReason, lastDays, latestValue, strikeFacts } from "@/features/market/derivatives-model";
import { EarningsBody, revenueFactReason } from "./earnings-tab";
import { EventsBody } from "./events-tab";
import {
  FACTORS_FULL,
  FACTORS_NONE,
  FUNDAMENTALS_EMPTY,
  FUNDAMENTALS_FULL,
  FUNDAMENTALS_LOSS,
  FUNDAMENTALS_PARTIAL,
  FUTURES_FULL,
  FUTURES_NONE,
  HOLDERS_EMPTY,
  HOLDERS_FULL,
  HOLDERS_PARTIAL,
  MARKET_DERIVATIVES_EMPTY,
  MARKET_DERIVATIVES_FULL,
  SHORT_EMPTY,
  SHORT_FULL,
  SHORT_OTC,
} from "./fixtures";
import { FuturesBody } from "./futures-tab";
import { HoldersBody } from "./holders-tab";
import { KeyFacts } from "./key-facts";
import { ShortBody } from "./short-tab";
import { ValuationBody, valuationMetrics } from "./valuation-tab";

const html = (node: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(node);

describe("KeyFacts", () => {
  it("shows rank, highlights, flags and labelled score bars", () => {
    const out = html(createElement(KeyFacts, { factors: FACTORS_FULL }));
    expect(out).toContain("綜合排名 312 / 1,947");
    expect(out).toContain("外資連買 5 日");
    expect(out).toContain("注意股");
    expect(out).toContain('aria-label="資金流向"');
    expect(out).toContain('aria-valuenow="82"');
    expect(out).not.toContain("accumulation");
  });
  it("explains itself when the stock is not in the snapshot", () => {
    expect(html(createElement(KeyFacts, { factors: FACTORS_NONE }))).toContain("選股快照裡還沒有這檔");
    expect(html(createElement(KeyFacts, { factors: { ...FACTORS_NONE, notice: "快照尚未產生" } }))).toContain("快照尚未產生");
  });
});

describe("ValuationBody", () => {
  it("full: values, percentile wording and a labelled chart per metric", () => {
    const out = html(createElement(ValuationBody, { data: FUNDAMENTALS_FULL }));
    for (const label of ["本益比", "股價淨值比", "殖利率"]) expect(out).toContain(label);
    expect(out).toContain("位於近一年 23% 分位");
    expect(out).toContain("位於近一年 71% 分位");
    expect(out).toContain("22.19");
    expect(out).toContain("本益比歷史（週）（本益比）：最新");
  });
  it("partial: no percentile bars, one-line reason, short-history note", () => {
    const out = html(createElement(ValuationBody, { data: FUNDAMENTALS_PARTIAL }));
    expect(out).not.toContain("分位</span>");
    expect(out).toContain("累積到 60 筆才計算分位");
    expect(out).toContain("歷史僅 2 週");
  });
  it("loss-making: says so instead of a P/E", () => {
    const out = html(createElement(ValuationBody, { data: FUNDAMENTALS_LOSS }));
    expect(out).toContain("虧損，無本益比");
    expect(valuationMetrics(FUNDAMENTALS_LOSS.valuation!)[0].value).toBeNull();
  });
  it("empty: reason line", () => {
    expect(html(createElement(ValuationBody, { data: FUNDAMENTALS_EMPTY }))).toContain("還沒有交易所公布");
  });
});

describe("EarningsBody", () => {
  it("full: facts, 億 conversion, tables and charts", () => {
    const out = html(createElement(EarningsBody, { data: FUNDAMENTALS_FULL }));
    expect(out).toContain("+52.3%");
    expect(out).toContain("是"); // 12-month high
    expect(out).toContain("9 個月");
    expect(out).toContain("98.55 元");
    expect(out).toContain("33.8%");
    expect(out).toContain("月營收與年增率（月營收（億））：最新");
    expect(out).toContain("<table");
  });
  it("partial (real 2330 payload): converts 仟元 to 億 and explains the blank year-on-year tiles", () => {
    const out = html(createElement(EarningsBody, { data: FUNDAMENTALS_PARTIAL }));
    expect(out).toContain("5,868.9 億");
    expect(out).toContain("14,942.4"); // 2026Q3 revenue in 億 (table column, no unit)
    expect(out).toContain("目前只有 4 個月");
    expect(out).toContain("連續四季財報");
    expect(revenueFactReason(FUNDAMENTALS_PARTIAL.revenue!)).toContain("24 個月");
    expect(revenueFactReason(FUNDAMENTALS_FULL.revenue!)).toBeNull();
  });
  it("empty: both reasons", () => {
    const out = html(createElement(EarningsBody, { data: FUNDAMENTALS_EMPTY }));
    expect(out).toContain("月營收資料");
    expect(out).toContain("季度財報資料");
  });
});

describe("HoldersBody", () => {
  it("full: weekly change in percentage points, foreign holding, market cap with a close", () => {
    const out = html(createElement(HoldersBody, { data: HOLDERS_FULL, close: 2210 }));
    expect(out).toContain("86.91%");
    expect(out).toContain("較上週 +0.21 個百分點");
    expect(out).toContain("67.55%");
    expect(out).toContain("259.3 億股");
    expect(out).toContain("市值約");
  });
  it("partial: one week → no weekly change, history caveat, no market cap without a close", () => {
    const out = html(createElement(HoldersBody, { data: HOLDERS_PARTIAL, close: null }));
    expect(out).toContain("週變化需要兩週資料");
    expect(out).toContain("目前只有 1 週資料");
    expect(out).toContain("不計市值");
  });
  it("empty: reason from the server note or the built-in line", () => {
    expect(html(createElement(HoldersBody, { data: HOLDERS_EMPTY, close: null }))).toContain("2026-11-20 起累積");
  });
});

describe("ShortBody", () => {
  it("full: tiles, pledge and day-trade chart", () => {
    const out = html(createElement(ShortBody, { data: SHORT_FULL }));
    expect(out).toContain("借券賣出餘額");
    expect(out).toContain("0.07%");
    expect(out).toContain("0.33 天");
    expect(out).toContain("0.10%");
    expect(out).toContain("當沖比（每日）");
    expect(out).toContain("借券賣出餘額與融券餘額");
  });
  it("OTC: explains that no per-stock day-trade data is published, and a missing pledge record", () => {
    const out = html(createElement(ShortBody, { data: SHORT_OTC }));
    expect(out).toContain("上櫃個股官方沒有公布個股當沖成交量");
    expect(out).toContain("尚無董監持股申報資料");
    expect(out).not.toContain("當沖比（每日）");
  });
  it("empty: reason line instead of a chart", () => {
    expect(html(createElement(ShortBody, { data: SHORT_EMPTY }))).toContain("還沒有這檔的借券與當沖資料");
  });
});

describe("FuturesBody", () => {
  it("has futures: tiles, OI / volume chart and basis chart with a gap", () => {
    const out = html(createElement(FuturesBody, { data: FUTURES_FULL }));
    expect(out).toContain("+0.41%");
    expect(out).toContain("+5.01%");
    expect(out).toContain("個股期貨未平倉與成交量");
    expect(out).toContain("近月基差");
  });
  it("no stock futures: one compact line", () => {
    const out = html(createElement(FuturesBody, { data: FUTURES_NONE }));
    expect(out).toContain("這檔沒有個股期貨");
    expect(out).not.toContain("<figure");
  });
});

describe("EventsBody", () => {
  it("lists upcoming ex-dividend / ex-rights with days from the query date", () => {
    const out = html(createElement(EventsBody, { data: SHORT_FULL }));
    expect(out).toContain("2026-12-10");
    expect(out).toContain("13 天後");
    expect(out).toContain("7.00 元/股");
    expect(out).toContain("每千股配 50 股");
    expect(out).toContain("尚未公告");
  });
  it("empty: the 60-day line", () => {
    expect(html(createElement(EventsBody, { data: SHORT_OTC }))).toContain("未來 60 天沒有已公告的除權息");
  });
});

describe("market derivatives", () => {
  it("helpers: windowing, latest value, labelled strikes, empty reason", () => {
    const days = MARKET_DERIVATIVES_FULL.days;
    expect(lastDays(days, 20)).toHaveLength(20);
    expect(lastDays(days, 99)).toHaveLength(30);
    expect(latestValue(days, (d) => d.vix)?.date).toBe(days[29].date);
    expect(latestValue([], (d) => d.vix)).toBeNull();
    const strikes = strikeFacts(MARKET_DERIVATIVES_FULL.latest);
    expect(strikes.map((s) => s.role)).toEqual(["上方壓力參考", "下方支撐參考", "上方壓力參考"]);
    expect(strikeFacts(null)).toEqual([]);
    expect(derivativesEmptyReason(null, 0)).toContain("回補");
    expect(derivativesEmptyReason("2026-11-27", 0)).toContain("之前還沒有");
    expect(derivativesEmptyReason("2026-11-27", 3)).toBeNull();
  });
  it("renders the tiles with Taiwan tones and factual strike labels", () => {
    const out = html(createElement(DerivativesBody, { data: MARKET_DERIVATIVES_FULL, range: 20 }));
    expect(out).toContain("外資台指期淨未平倉（大台等值，口）");
    expect(out).toContain("text-down"); // net short
    expect(out).toContain("淨空單");
    expect(out).toContain("買權未平倉最大履約價");
    expect(out).toContain("上方壓力參考");
    expect(out).toContain("不是預測");
    expect(out).toContain("VIX");
  });
  it("section shows its loading skeleton, never throws, before data arrives", () => {
    expect(html(createElement(DerivativesSection, { asOf: "2026-11-27" }))).toContain("期貨與選擇權");
    expect(derivativesEmptyReason(MARKET_DERIVATIVES_EMPTY.data_through, MARKET_DERIVATIVES_EMPTY.days.length)).not.toBeNull();
  });
});
