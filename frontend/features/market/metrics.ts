import type { MarketMetric, MarketScope } from "@/lib/api/types";

export type MetricKind = "flow" | "volume" | "price" | "credit";

export type MetricInfo = {
  label: string;
  kind: MetricKind;
  /** Board titles: the leading list (order=desc) and, when meaningful, the trailing one (order=asc). */
  top: string;
  bottom: string | null;
  /** Which exchange report the metric depends on, for the "not published yet" notice. */
  report: string | null;
};

export const METRICS: Record<MarketMetric, MetricInfo> = {
  foreign_net: { label: "外資", kind: "flow", top: "外資買超", bottom: "外資賣超", report: "三大法人" },
  trust_net: { label: "投信", kind: "flow", top: "投信買超", bottom: "投信賣超", report: "三大法人" },
  dealer_net: { label: "自營商", kind: "flow", top: "自營商買超", bottom: "自營商賣超", report: "三大法人" },
  total_net: {
    label: "三大法人合計",
    kind: "flow",
    top: "三大法人買超",
    bottom: "三大法人賣超",
    report: "三大法人",
  },
  volume: { label: "成交量", kind: "volume", top: "成交量排行", bottom: null, report: null },
  turnover: { label: "成交值", kind: "volume", top: "成交值排行", bottom: null, report: null },
  change_pct: { label: "漲跌幅", kind: "price", top: "漲幅排行", bottom: "跌幅排行", report: null },
  margin_change: { label: "融資增減", kind: "credit", top: "融資增加", bottom: "融資減少", report: "融資融券" },
  short_change: { label: "融券增減", kind: "credit", top: "融券增加", bottom: "融券減少", report: "融資融券" },
};

export const METRIC_GROUPS: { id: string; label: string; metrics: MarketMetric[] }[] = [
  { id: "institutional", label: "法人", metrics: ["foreign_net", "trust_net", "dealer_net", "total_net"] },
  { id: "volume", label: "成交", metrics: ["volume", "turnover"] },
  { id: "price", label: "價格", metrics: ["change_pct"] },
  { id: "credit", label: "信用", metrics: ["margin_change", "short_change"] },
];

export const SCOPES: { value: MarketScope; label: string }[] = [
  { value: "ALL", label: "全部" },
  { value: "TW", label: "上市" },
  { value: "TWO", label: "上櫃" },
];

export const SCOPE_NAME: Record<MarketScope, string> = {
  ALL: "上市＋上櫃",
  TW: "上市",
  TWO: "上櫃",
};

export const WINDOWS = [1, 5, 10, 20] as const;
export const FLOW_RANGES = [20, 60] as const;

export const windowLabel = (window: number) => (window === 1 ? "當日" : `近 ${window} 日`);

export function isMetric(value: string | null): value is MarketMetric {
  return value !== null && value in METRICS;
}
