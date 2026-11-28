/** Test-only payloads shaped exactly like the backend responses (full, partial = 2330 on 2026-11-27, empty). */
import type {
  MarketDerivatives,
  StockFactors,
  StockFundamentals,
  StockFutures,
  StockHolders,
  StockShortInterest,
} from "@/lib/api/profile";

const months = (count: number, endYear = 2026, endMonth = 10) =>
  Array.from({ length: count }, (_, i) => {
    const offset = count - 1 - i;
    const index = endYear * 12 + (endMonth - 1) - offset;
    const year = Math.floor(index / 12);
    const month = (index % 12) + 1;
    return {
      period: `${year}-${String(month).padStart(2, "0")}`,
      revenue: 300_000_000 + i * 6_000_000,
      yoy_pct: i < 12 ? null : 10 + (i % 7) * 3 - (i === 20 ? 40 : 0),
      mom_pct: (i % 5) - 2,
    };
  });

export const FUNDAMENTALS_FULL: StockFundamentals = {
  symbol: "2330.TW",
  as_of: "2026-11-27",
  valuation: {
    date: "2026-11-27",
    pe_ratio: 22.19,
    pb_ratio: 8.08,
    dividend_yield_pct: 1.01,
    pe_percentile_250d: 23.4,
    pb_percentile_250d: 71,
    yield_percentile_250d: 8,
    history: Array.from({ length: 40 }, (_, i) => ({
      date: `2026-${String(1 + Math.floor(i / 4)).padStart(2, "0")}-${String(1 + (i % 4) * 7).padStart(2, "0")}`,
      pe_ratio: 18 + (i % 9) * 0.6,
      pb_ratio: 6 + (i % 7) * 0.3,
      dividend_yield_pct: 1 + (i % 5) * 0.1,
    })),
  },
  revenue: {
    months: months(36),
    yoy_3m_pct: 52.3,
    yoy_12m_pct: -4.2,
    is_12m_high: true,
    growth_streak_months: 9,
  },
  earnings: {
    quarters: Array.from({ length: 8 }, (_, i) => ({
      period: `${2025 + Math.floor((i + 1) / 4)}Q${((i + 1) % 4) + 1}`,
      revenue: 1_000_000_000 + i * 80_000_000,
      gross_margin_pct: 55 + i,
      operating_margin_pct: 45 + i,
      net_margin_pct: 40 + i,
      eps: 12 + i * 2.1,
    })),
    eps_ttm: 98.55,
    eps_yoy_pct: 41.2,
    roe_ttm_pct: 33.8,
  },
  notes: ["月營收單位為新台幣仟元；年增率／月增率為公告值。"],
};

/** What production returned for 2330.TW on 2026-11-27: almost everything still accumulating. */
export const FUNDAMENTALS_PARTIAL: StockFundamentals = {
  symbol: "2330.TW",
  as_of: "2026-11-27",
  valuation: {
    date: "2026-11-27",
    pe_ratio: 22.19,
    pb_ratio: 8.08,
    dividend_yield_pct: 1.01,
    pe_percentile_250d: null,
    pb_percentile_250d: null,
    yield_percentile_250d: null,
    history: [
      { date: "2026-11-20", pe_ratio: 20.36, pb_ratio: 7.41, dividend_yield_pct: 1.1 },
      { date: "2026-11-27", pe_ratio: 22.19, pb_ratio: 8.08, dividend_yield_pct: 1.01 },
    ],
  },
  revenue: {
    months: [
      { period: "2026-07", revenue: 467_580_548, yoy_pct: 44.68, mom_pct: 5.62 },
      { period: "2026-08", revenue: 514_805_337, yoy_pct: 53.32, mom_pct: 10.09 },
      { period: "2026-09", revenue: 511_857_399, yoy_pct: 54.64, mom_pct: -0.57 },
      { period: "2026-10", revenue: 586_890_319, yoy_pct: 59.709757600700904, mom_pct: 14.658949962741477 },
    ],
    yoy_3m_pct: null,
    yoy_12m_pct: null,
    is_12m_high: null,
    growth_streak_months: 4,
  },
  earnings: {
    quarters: [
      { period: "2026Q2", revenue: null, gross_margin_pct: null, operating_margin_pct: null, net_margin_pct: null, eps: null },
      { period: "2026Q3", revenue: 1_494_243_284, gross_margin_pct: 66.41, operating_margin_pct: 58.65, net_margin_pct: 51.07, eps: 29.42 },
    ],
    eps_ttm: null,
    eps_yoy_pct: null,
    roe_ttm_pct: null,
  },
  notes: ["本系統的估值歷史自啟用日起逐步回補，尚不足 250 個交易日，百分位的觀察期較短。"],
};

/** A loss-making company with nothing else yet. */
export const FUNDAMENTALS_LOSS: StockFundamentals = {
  ...FUNDAMENTALS_PARTIAL,
  valuation: { ...FUNDAMENTALS_PARTIAL.valuation!, pe_ratio: null, history: [{ date: "2026-11-27", pe_ratio: null, pb_ratio: 1.2, dividend_yield_pct: null }], dividend_yield_pct: null },
};

export const FUNDAMENTALS_EMPTY: StockFundamentals = {
  symbol: "9999.TW",
  as_of: "2026-11-27",
  valuation: null,
  revenue: null,
  earnings: null,
  notes: [],
};

const holder = (date: string, p400: number, p1000: number) => ({
  data_date: date,
  pct_ge400: p400,
  pct_ge1000: p1000,
  holders_ge400: 2609,
  holders_ge1000: 1469,
  retail_holders: 3_278_245,
  retail_pct: 6.3492,
  total_holders: 3_329_043,
  total_shares: 25_932_370_067,
  available_at: "2026-11-27T15:05:00Z",
});

export const HOLDERS_PARTIAL: StockHolders = {
  symbol: "2330.TW",
  as_of: "2026-11-27",
  series: [holder("2026-11-20", 86.9127, 84.1458)],
  shares_outstanding: 25_932_364_970,
  foreign_shares: 17_517_636_906,
  foreign_holding_pct: 67.55,
  foreign_data_date: "2026-11-27",
  note: "集保戶股權分散表（每週五基準，週末公布）自本系統啟用日起累積。",
};

export const HOLDERS_FULL: StockHolders = {
  ...HOLDERS_PARTIAL,
  series: [holder("2026-10-30", 86.1, 83.2), holder("2026-11-06", 86.4, 83.6), holder("2026-11-13", 86.7, 83.9), holder("2026-11-20", 86.9127, 84.1458)],
};

export const HOLDERS_EMPTY: StockHolders = {
  symbol: "9999.TW",
  as_of: "2026-11-27",
  series: [],
  shares_outstanding: null,
  foreign_shares: null,
  foreign_holding_pct: null,
  foreign_data_date: null,
  note: "",
};

const shortDays = (count: number, withDayTrade = true) =>
  Array.from({ length: count }, (_, i) => ({
    date: `2026-11-${String(2 + i).padStart(2, "0")}`,
    sbl_balance_lots: 17_000 + i * 20,
    sbl_sold_lots: 100 + i,
    sbl_returned_lots: 10 + i,
    margin_short_balance_lots: 20 + i,
    day_trade_ratio_pct: withDayTrade ? 18 + (i % 6) : null,
  }));

export const SHORT_FULL: StockShortInterest = {
  symbol: "2330.TW",
  as_of: "2026-11-27",
  days: shortDays(20),
  sbl_change_5d_pct: 1.2,
  sbl_change_20d_pct: -3.4,
  sbl_pct_of_shares: 0.07,
  days_to_cover: 0.3346,
  day_trade_ratio_5d_pct: 24.51,
  day_trade_ratio_20d_pct: 21.3,
  insiders: { symbol: "2330.TW", period: "2026-10", pledge_ratio_pct: 0.1014, holding_shares: 1_704_375_505, pledged_shares: 1_728_000 },
  upcoming_events: [
    { date: "2026-12-10", kind: "ex_dividend", label: "除息 現金股利 7 元/股", cash_dividend: 7.000001, stock_dividend: 0 },
    { date: "2027-01-05", kind: "ex_rights", label: "除權", cash_dividend: null, stock_dividend: 0.05 },
  ],
  notes: ["除權息資料為交易所預告表，金額以發行公司公告為準。", "借券賣出餘額為交易所公告。"],
};

/** An OTC stock: no per-stock day-trade data, no pledge record, nothing upcoming. */
export const SHORT_OTC: StockShortInterest = {
  ...SHORT_FULL,
  symbol: "6488.TWO",
  days: shortDays(5, false),
  sbl_change_20d_pct: null,
  day_trade_ratio_5d_pct: null,
  day_trade_ratio_20d_pct: null,
  insiders: null,
  upcoming_events: [],
};

export const SHORT_EMPTY: StockShortInterest = {
  ...SHORT_OTC,
  days: [],
  sbl_change_5d_pct: null,
  sbl_pct_of_shares: null,
  days_to_cover: null,
};

export const FUTURES_FULL: StockFutures = {
  symbol: "2330.TW",
  has_futures: true,
  days: Array.from({ length: 10 }, (_, i) => ({
    date: `2026-11-${String(16 + i).padStart(2, "0")}`,
    open_interest: 35_000 - i * 400,
    volume: 30_000 + i * 500,
    near_month: "202612",
    near_settlement: 2200 - i,
    basis_pct: i === 3 ? null : -0.27 + i * 0.1,
  })),
  oi_change_1d: 0.41,
  oi_change_5d: 5.01,
  notes: ["未平倉量與成交量以「標準契約等值口」計。"],
};

export const FUTURES_NONE: StockFutures = { symbol: "1101.TW", has_futures: false, days: [], oi_change_1d: null, oi_change_5d: null, notes: [] };

export const FACTORS_FULL: StockFactors = {
  version: "screener_v1",
  symbol: "2330.TW",
  as_of: "2026-11-27",
  data_through: "2026-11-27",
  flow_through: "2026-11-27",
  universe: 1947,
  notice: null,
  entry: {
    rank: 312,
    symbol: "2330.TW",
    name: "台積電",
    market: "TW",
    industry_code: "24",
    industry_name: "半導體業",
    close: 2210,
    change_pct: 0.5,
    avg_turnover_20d: 4e10,
    composite: 71.2,
    scores: { momentum: 82.4, flow: 61, fundamental: 90, stability: 44, accumulation: null },
    metrics: {},
    highlights: ["外資連買 5 日", "營收年增 59.7%"],
    flags: ["attention"],
  },
};

/** The real database has no screener snapshot: entry is null. */
export const FACTORS_NONE: StockFactors = {
  version: "screener_v1",
  symbol: "2330.TW",
  as_of: "2026-11-27",
  data_through: null,
  flow_through: null,
  universe: 0,
  notice: null,
  entry: null,
};

export const MARKET_DERIVATIVES_FULL: MarketDerivatives = {
  as_of: "2026-11-27",
  data_through: "2026-11-27",
  days: Array.from({ length: 30 }, (_, i) => ({
    date: `2026-10-${String(1 + i).padStart(2, "0")}`,
    foreign_tx_net_oi: -100_000 + i * 3000,
    foreign_tx_equiv_net_oi: -100_000 + i * 3000 + 800,
    foreign_net_oi_change: i === 0 ? null : 3000,
    trust_tx_net_oi: 104_355,
    dealer_tx_net_oi: -3566,
    pc_volume_ratio: 82.07,
    pc_oi_ratio: 46.67 + i * 0.2,
    foreign_call_net_oi: 13_016,
    foreign_put_net_oi: 3661,
    top5_net_oi: 33_187,
    top10_net_oi: 13_190,
    vix: 20 + (i % 6),
  })),
  latest: {
    date: "2026-10-30",
    foreign_tx_net_oi: -13_000,
    foreign_tx_equiv_net_oi: -12_200,
    foreign_net_oi_change: 3000,
    trust_tx_net_oi: 104_355,
    dealer_tx_net_oi: -3566,
    pc_volume_ratio: 82.07,
    pc_oi_ratio: 52.4,
    foreign_call_net_oi: 13_016,
    foreign_put_net_oi: 3661,
    top5_net_oi: 33_187,
    top10_net_oi: 13_190,
    vix: 24,
    max_call_oi_strike: 24000,
    max_put_oi_strike: 22000,
    max_call_oi_expiry: "202612",
    max_put_oi_expiry: "202612",
    weekly_max_call_oi_strike: 23800,
    weekly_max_put_oi_strike: null,
    weekly_expiry: "202612W1",
  },
  notes: ["期貨未平倉為各法人身份別於收盤後的多空未平倉口數；淨額＝多方－空方，僅描述部位，不是預測。"],
};

export const MARKET_DERIVATIVES_EMPTY: MarketDerivatives = {
  as_of: "2026-11-27",
  data_through: null,
  days: [],
  latest: null,
  notes: [],
};
