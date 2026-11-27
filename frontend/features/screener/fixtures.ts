import type { ScreenerEntry, ScreenerMeta, ScreenerResponse } from "@/lib/api/types";

/** A trimmed copy of what GET /market/screener/meta returns (SCREENER_SPEC.md §4). Test-only. */
export const META: ScreenerMeta = {
  version: "screener_v1",
  factors: [
    { key: "momentum", label: "價格動能", kind: "score", group: "score", unit: "分", description: "近期漲幅與接近 52 週高點的程度。", higher_is_better: true, inputs: [{ key: "ret_20d", weight: 0.3 }, { key: "ret_60d", weight: 0.3 }, { key: "ret_120d", weight: 0.2 }, { key: "high_52w_gap_pct", weight: 0.2 }] },
    { key: "flow", label: "資金流向", kind: "score", group: "score", unit: "分", description: "法人買超佔成交量、買超天數與融資變化。", higher_is_better: true, inputs: [{ key: "inst_flow_ratio_20d", weight: 0.3 }, { key: "inst_flow_ratio_60d", weight: 0.25 }, { key: "inst_buy_days_20d", weight: 0.15 }, { key: "cmf_20", weight: 0.15 }, { key: "margin_change_20d_pct", weight: 0.15, invert: true }] },
    { key: "fundamental", label: "基本面", kind: "score", group: "score", unit: "分", description: "營收年增率。", higher_is_better: true, inputs: [{ key: "revenue_yoy_pct", weight: 0.6 }, { key: "revenue_cum_yoy_pct", weight: 0.4 }] },
    { key: "stability", label: "穩定度", kind: "score", group: "score", unit: "分", description: "波動與回撤越小越高。", higher_is_better: true, inputs: [{ key: "volatility_20d", weight: 0.5, invert: true }, { key: "max_drawdown_60d", weight: 0.5 }] },
  ],
  metrics: [
    { key: "ret_20d", label: "20 日報酬", group: "price", unit: "%", decimals: 1, description: "", higher_is_better: true },
    { key: "ret_60d", label: "60 日報酬", group: "price", unit: "%", decimals: 1, description: "", higher_is_better: true },
    { key: "ret_120d", label: "120 日報酬", group: "price", unit: "%", decimals: 1, description: "", higher_is_better: true },
    { key: "high_52w_gap_pct", label: "距 52 週高點", group: "price", unit: "%", decimals: 1, description: "", higher_is_better: true },
    { key: "trend_aligned", label: "多頭排列", group: "price", unit: "bool", decimals: null, description: "收盤 > MA20 > MA60 > MA120。", higher_is_better: true, kind: "bool" },
    { key: "total_net_20d", label: "法人 20 日合計買賣超", group: "flow", unit: "張", decimals: 0, description: "", higher_is_better: true },
    { key: "inst_flow_ratio_20d", label: "法人 20 日買超佔成交量", short: "法人20日佔量", group: "flow", unit: "%", decimals: 1, description: "", higher_is_better: true },
    { key: "inst_flow_ratio_60d", label: "法人 60 日買超佔成交量", group: "flow", unit: "%", decimals: 1, description: "", higher_is_better: true },
    { key: "inst_buy_days_20d", label: "法人 20 日買超天數占比", group: "flow", unit: "%", decimals: 0, description: "", higher_is_better: true },
    { key: "foreign_streak", label: "外資連買賣", group: "flow", unit: "日", decimals: 0, description: "", higher_is_better: true },
    { key: "trust_streak", label: "投信連買賣", group: "flow", unit: "日", decimals: 0, description: "", higher_is_better: true },
    { key: "cmf_20", label: "資金流量指標 CMF", group: "flow", unit: "", decimals: 2, description: "", higher_is_better: true },
    { key: "margin_change_20d_pct", label: "融資 20 日增減", group: "credit", unit: "%", decimals: 1, description: "", higher_is_better: false },
    { key: "chip_transfer_20d", label: "籌碼轉強（法人買、融資退）", group: "credit", unit: "bool", decimals: null, description: "", higher_is_better: true, kind: "bool" },
    { key: "revenue_yoy_pct", label: "營收年增率", group: "fundamental", unit: "%", decimals: 1, description: "", higher_is_better: true },
    { key: "revenue_cum_yoy_pct", label: "累計營收年增率", group: "fundamental", unit: "%", decimals: 1, description: "", higher_is_better: true },
    { key: "volatility_20d", label: "20 日年化波動", group: "risk", unit: "%", decimals: 1, description: "", higher_is_better: false },
    { key: "max_drawdown_60d", label: "60 日最大回撤", group: "risk", unit: "%", decimals: 1, description: "", higher_is_better: true },
    { key: "avg_turnover_20d", label: "20 日均成交值", group: "liquidity", unit: "元", decimals: 0, description: "", higher_is_better: true },
  ],
  groups: [
    { key: "score", label: "因子分數" },
    { key: "price", label: "價格動能" },
    { key: "flow", label: "資金流向" },
    { key: "credit", label: "信用交易" },
    { key: "fundamental", label: "營收" },
    { key: "risk", label: "風險" },
    { key: "liquidity", label: "流動性" },
  ],
  presets: [
    { key: "balanced", label: "綜合評分", description: "多因子綜合分數由高到低。", sort: "composite", order: "desc", filters: [{ key: "avg_turnover_20d", min: 20000000 }], columns: ["composite", "momentum", "flow", "fundamental", "stability"] },
    { key: "money_flow", label: "資金流入", description: "法人持續買超、資金流向分數最高。", sort: "flow", order: "desc", filters: [{ key: "inst_flow_ratio_20d", min: 0 }, { key: "avg_turnover_20d", min: 20000000 }], columns: ["flow", "total_net_20d", "inst_flow_ratio_20d", "foreign_streak", "trust_streak", "margin_change_20d_pct"] },
    { key: "quiet_accumulation", label: "低調吸籌", description: "法人穩定買進、股價尚未大漲。", sort: "accumulation", order: "desc", filters: [{ key: "inst_flow_ratio_60d", min: 0 }, { key: "inst_buy_days_60d", min: 55 }, { key: "ret_20d", min: -8, max: 8 }, { key: "avg_turnover_20d", min: 10000000 }], columns: ["accumulation", "inst_flow_ratio_60d", "inst_buy_days_60d", "ret_20d", "ret_60d", "cmf_20"] },
    { key: "momentum", label: "強勢動能", description: "多頭排列且動能分數最高。", sort: "momentum", order: "desc", filters: [{ key: "trend_aligned", flag: true }, { key: "avg_turnover_20d", min: 50000000 }], columns: ["momentum", "ret_20d", "ret_60d", "high_52w_gap_pct"] },
  ],
  industries: [
    { code: "24", name: "半導體業", count: 178 },
    { code: "25", name: "電腦及週邊設備業", count: 64 },
    { code: "17", name: "金融保險業", count: 41 },
  ],
};

const entry = (over: Partial<ScreenerEntry> & Pick<ScreenerEntry, "rank" | "symbol" | "name">): ScreenerEntry => ({
  market: "TW",
  industry_code: "24",
  industry_name: "半導體業",
  close: 100,
  change_pct: 0.5,
  avg_turnover_20d: 1.2e9,
  composite: 60,
  scores: { momentum: 60, flow: 60, fundamental: 60, stability: 60, accumulation: 55 },
  metrics: {},
  highlights: [],
  flags: [],
  ...over,
});

const flowMetrics = {
  ret_20d: 3.1, ret_60d: 12.4, ret_120d: 30.2, high_52w_gap_pct: -1.8, trend_aligned: true,
  total_net_20d: 12345, inst_flow_ratio_20d: 4.8, inst_flow_ratio_60d: 3.2, inst_buy_days_20d: 65,
  foreign_streak: -12, trust_streak: 5, cmf_20: 0.18, margin_change_20d_pct: -4.1, chip_transfer_20d: true,
  revenue_yoy_pct: 35.2, revenue_cum_yoy_pct: 28.1, volatility_20d: 24.3, max_drawdown_60d: -9.8,
};

/** Eight rows: missing values, flags, a long name, no industry, no highlights, many highlights. */
export const ENTRIES: ScreenerEntry[] = [
  entry({ rank: 1, symbol: "2330.TW", name: "台積電", close: 2180, change_pct: 1.63, avg_turnover_20d: 9.1e10, composite: 71.2, scores: { momentum: 64, flow: 88.3, fundamental: 61, stability: 55.4, accumulation: 70.2 }, metrics: flowMetrics, highlights: ["投信連買 5 日", "法人 20 日買超佔成交量 4.8%", "融資 20 日減 4.1%", "營收年增 35.2%"] }),
  entry({ rank: 2, symbol: "6488.TWO", name: "環球晶圓與其他非常長的公司名稱股份有限公司", market: "TWO", close: 412.5, change_pct: -2.1, composite: 66.8, scores: { momentum: 40, flow: 81, fundamental: 70, stability: 62, accumulation: 66 }, metrics: { ...flowMetrics, ret_20d: -4.2, foreign_streak: 8 }, highlights: ["外資連買 8 日"] }),
  entry({ rank: 3, symbol: "3008.TW", name: "大立光", close: 2305, change_pct: 0, composite: 60.1, scores: { momentum: 50, flow: 72, fundamental: null, stability: 70, accumulation: 61 }, metrics: { ...flowMetrics, revenue_yoy_pct: null, revenue_cum_yoy_pct: null, margin_change_20d_pct: null, chip_transfer_20d: null }, highlights: ["法人買、融資退（籌碼轉強）"] }),
  entry({ rank: 4, symbol: "8299.TWO", name: "群聯", market: "TWO", close: 561, change_pct: 3.4, industry_code: null, industry_name: null, composite: 58, scores: { momentum: 77, flow: 66, fundamental: 55, stability: 30, accumulation: 40 }, metrics: flowMetrics, flags: ["attention"] }),
  entry({ rank: 5, symbol: "2317.TW", name: "鴻海", close: 215, change_pct: -0.46, industry_code: "25", industry_name: "電腦及週邊設備業", composite: 55.5, metrics: { ...flowMetrics, total_net_20d: -50210 }, highlights: ["外資連賣 12 日", "距 52 週高點 1.8%"], flags: ["disposition"] }),
  entry({ rank: 6, symbol: "2412.TW", name: "中華電", close: 128.5, change_pct: null, composite: null, scores: { momentum: null, flow: null, fundamental: null, stability: null, accumulation: null }, metrics: {}, highlights: [] }),
  entry({ rank: 7, symbol: "2881.TW", name: "富邦金", close: 88.1, change_pct: 0.34, industry_code: "17", industry_name: "金融保險業", composite: 52, metrics: { ...flowMetrics, trend_aligned: false }, highlights: ["營收年增 12.0%", "投信連買 3 日", "法人 60 日買超佔成交量 2.0%", "距 52 週高點 0.4%"] }),
  entry({ rank: 8, symbol: "1101.TW", name: "台泥", close: 31.2, change_pct: -1.1, industry_code: "17", industry_name: "水泥工業", composite: 47.3, metrics: flowMetrics }),
];

export const RESPONSE: ScreenerResponse = {
  version: "screener_v1", market: "ALL", as_of: "2026-11-27", data_through: "2026-11-27", flow_through: "2026-11-26",
  preset: "balanced", sort: "composite", order: "desc", universe: 1953, matched: 212, limit: 50, offset: 0,
  snapshot_pending: false, entries: ENTRIES,
};
