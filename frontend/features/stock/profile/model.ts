/** Pure helpers behind 「個股全貌」: unit conversion, wording, empty-state reasons, tabs, key facts. */
import type { ScreenerEntry, ScreenerMeta } from "@/lib/api/types";
import type { ShortDay, StockFutures, StockHolders, StockShortInterest, UpcomingEvent } from "@/lib/api/profile";
import { formatNumber, formatSigned } from "@/lib/format";

/* ---- Units ---------------------------------------------------------------------------- */

/** NT$ thousands (千元, as statements and monthly revenue are published) → 億 NT$. */
export const thousandsToYi = (thousands: number): number => thousands / 1e5;

export const formatYiFromThousands = (thousands: number | null | undefined, digits = 1): string =>
  thousands === null || thousands === undefined ? "—" : `${formatNumber(thousandsToYi(thousands), digits)} 億`;

/** NT$ → 億 with one decimal. */
export const formatYiFromTwd = (twd: number, digits = 1): string => `${formatNumber(twd / 1e8, digits)} 億`;

export const formatPercent = (value: number | null | undefined, digits = 1): string =>
  value === null || value === undefined ? "—" : `${formatNumber(value, digits)}%`;

export const formatSignedPercent = (value: number | null | undefined, digits = 1): string =>
  value === null || value === undefined ? "—" : `${formatSigned(value, digits)}%`;

export const formatInt = (value: number | null | undefined): string =>
  value === null || value === undefined ? "—" : Math.round(value).toLocaleString("zh-TW");

export const formatSignedInt = (value: number | null | undefined): string =>
  value === null || value === undefined ? "—" : formatSigned(Math.round(value), 0);

/** Taiwan tone class: positive red, negative green, zero / missing neutral. */
export const toneOf = (value: number | null | undefined): "text-up" | "text-down" | "" =>
  value === null || value === undefined || value === 0 ? "" : value > 0 ? "text-up" : "text-down";

/** Shares outstanding × close → NT$ (null when either is missing). */
export const marketCap = (shares: number | null, close: number | null | undefined): number | null =>
  shares === null || close === null || close === undefined ? null : shares * close;

/** "2026-11" → "26/11" for crowded axes. */
export const monthLabel = (period: string): string => `${period.slice(2, 4)}/${period.slice(5, 7)}`;

/* ---- Wording --------------------------------------------------------------------------- */

/** 「位於近一年 23% 分位」; null when there is no percentile. */
export function percentileText(percentile: number | null | undefined): string | null {
  if (percentile === null || percentile === undefined || !Number.isFinite(percentile)) return null;
  return `位於近一年 ${Math.round(Math.min(100, Math.max(0, percentile)))}% 分位`;
}

/** Why a percentile bar is missing, in one line. */
export function percentileReason(value: number | null | undefined, percentile: number | null | undefined): string | null {
  if (percentile !== null && percentile !== undefined) return null;
  if (value === null || value === undefined) return "目前沒有數值，無法比較。";
  return "近一年的觀測不足 60 筆，累積到 60 筆才計算分位。";
}

/** Whole calendar days from `from` to `to` (both YYYY-MM-DD); negative when `to` is earlier. */
export function daysUntil(to: string, from: string): number {
  const parse = (iso: string) => {
    const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
    return Date.UTC(y, m - 1, d);
  };
  return Math.round((parse(to) - parse(from)) / 86_400_000);
}

export const daysUntilText = (days: number): string =>
  days === 0 ? "今天" : days > 0 ? `${days} 天後` : `${-days} 天前`;

export const EVENT_KIND_LABEL: Record<UpcomingEvent["kind"], string> = {
  ex_dividend: "除息",
  ex_rights: "除權",
  ex_rights_dividend: "除權息",
  investor_conference: "法說會",
};

/** 0.05 new shares per share → "每千股配 50 股". */
export function stockDividendText(perShare: number | null): string {
  if (perShare === null || perShare === 0) return "—";
  return `每千股配 ${formatNumber(perShare * 1000, 0)} 股`;
}

/* ---- Empty-state reasons ----------------------------------------------------------------- */

export type ReasonTopic =
  | "valuation"
  | "revenue"
  | "earnings"
  | "holders"
  | "short"
  | "daytrade-otc"
  | "daytrade"
  | "futures"
  | "events"
  | "factors";

const REASONS: Record<ReasonTopic, string> = {
  valuation: "這檔還沒有交易所公布的本益比／股價淨值比資料，估值歷史自啟用日起逐步累積。",
  revenue: "還沒有這檔的月營收資料（新上市或尚未申報）。",
  earnings: "還沒有這檔的季度財報資料（新上市或尚未申報）。",
  holders: "集保股權分散表每週五基準、週末公布，本系統自 2026-11-20 起累積，這檔還沒有資料。",
  short: "還沒有這檔的借券與當沖資料，資料自啟用日起逐日累積。",
  "daytrade-otc": "上櫃個股官方沒有公布個股當沖成交量，因此沒有當沖比。",
  daytrade: "尚未取得這檔的當沖資料（交易所通常於收盤後 T+1 補齊）。",
  futures: "這檔沒有個股期貨，所以沒有未平倉與基差資料。",
  events: "未來 60 天沒有已公告的除權息。",
  factors: "選股快照裡還沒有這檔（可能是新上市、歷史資料不足，或快照尚未產生）。",
};

/** The one-line reason a card is empty. A server `notice` / note wins when it explains it. */
export function emptyReason(topic: ReasonTopic, serverNote?: string | null): string {
  const note = serverNote?.trim();
  return note ? note : REASONS[topic];
}

/** Day-trade ratio availability: no values → why (OTC stocks are never published). */
export function dayTradeReason(symbol: string, days: ShortDay[]): string | null {
  if (days.some((d) => d.day_trade_ratio_pct !== null)) return null;
  return emptyReason(symbol.toUpperCase().endsWith(".TWO") ? "daytrade-otc" : "daytrade");
}

/** Every weekly point so far, with the one-line caveat about how short the history is. */
export function holdersHistoryNote(holders: StockHolders): string | null {
  const weeks = holders.series.length;
  if (weeks === 0) return null;
  if (weeks < 4) return `目前只有 ${weeks} 週資料（歷史自 2026-11-20 起，每週累積一筆），趨勢要再等幾週才看得出來。`;
  return null;
}

/** Latest-vs-previous weekly change in percentage points, null with fewer than two weeks. */
export function weeklyChange(series: StockHolders["series"], pick: (p: StockHolders["series"][number]) => number): number | null {
  if (series.length < 2) return null;
  return pick(series[series.length - 1]) - pick(series[series.length - 2]);
}

/** Average of the last `n` non-null day-trade ratios, null when there are none. */
export function recentAverage(days: ShortDay[], n: number): number | null {
  const values = days.map((d) => d.day_trade_ratio_pct).filter((v): v is number => v !== null).slice(-n);
  return values.length === 0 ? null : values.reduce((a, b) => a + b, 0) / values.length;
}

/** The latest futures day that has open interest. */
export function latestFuturesDay(futures: StockFutures) {
  for (let i = futures.days.length - 1; i >= 0; i -= 1) if (futures.days[i].open_interest !== null) return futures.days[i];
  return null;
}

/** Newest day of a short-interest response that carries a 借券 balance. */
export function latestShortDay(short: StockShortInterest): ShortDay | null {
  for (let i = short.days.length - 1; i >= 0; i -= 1) if (short.days[i].sbl_balance_lots !== null) return short.days[i];
  return null;
}

/* ---- Tabs -------------------------------------------------------------------------------- */

export const PROFILE_TABS = [
  { id: "valuation", label: "估值" },
  { id: "earnings", label: "營收獲利" },
  { id: "holders", label: "大戶與外資" },
  { id: "short", label: "放空與當沖" },
  { id: "futures", label: "期貨" },
  { id: "events", label: "事件" },
] as const;

export type ProfileTab = (typeof PROFILE_TABS)[number]["id"];
export const DEFAULT_TAB: ProfileTab = "valuation";

const HASH_KEY = "profile";

/** `#profile=earnings` → "earnings"; anything else → null. */
export function parseTabHash(hash: string): ProfileTab | null {
  const match = /^#?profile=([a-z]+)$/.exec(hash.trim());
  const id = match?.[1];
  return PROFILE_TABS.find((tab) => tab.id === id)?.id ?? null;
}

export const tabHash = (tab: ProfileTab): string => `#${HASH_KEY}=${tab}`;

/** Roving-tab keyboard movement; null when the key does not move. */
export function nextTab(current: ProfileTab, key: string): ProfileTab | null {
  const index = PROFILE_TABS.findIndex((tab) => tab.id === current);
  const last = PROFILE_TABS.length - 1;
  if (key === "ArrowRight") return PROFILE_TABS[index === last ? 0 : index + 1].id;
  if (key === "ArrowLeft") return PROFILE_TABS[index === 0 ? last : index - 1].id;
  if (key === "Home") return PROFILE_TABS[0].id;
  if (key === "End") return PROFILE_TABS[last].id;
  return null;
}

/* ---- Key facts (screener factors entry) --------------------------------------------------- */

export type ScoreBar = { key: string; label: string; value: number };

/** Labelled 0-100 scores in catalogue order (unknown keys last, null scores dropped). */
export function scoreBars(entry: ScreenerEntry, meta: ScreenerMeta | null): ScoreBar[] {
  const order = meta?.factors.map((f) => f.key) ?? [];
  const labelOf = (key: string) => {
    const factor = meta?.factors.find((f) => f.key === key);
    return factor?.short ?? factor?.label ?? key;
  };
  return Object.entries(entry.scores)
    .filter((pair): pair is [string, number] => pair[1] !== null && Number.isFinite(pair[1]))
    .sort(([a], [b]) => {
      const ia = order.indexOf(a);
      const ib = order.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    })
    .map(([key, value]) => ({ key, label: labelOf(key), value }));
}

/** 「綜合排名 312 / 1,947」; null when the stock is unranked. */
export function rankText(rank: number | null | undefined, universe: number): string | null {
  if (rank === null || rank === undefined) return null;
  return universe > 0
    ? `綜合排名 ${rank.toLocaleString("zh-TW")} / ${universe.toLocaleString("zh-TW")}`
    : `綜合排名 ${rank.toLocaleString("zh-TW")}`;
}
