import type { MarketDerivativesDay, MarketDerivativesLatest } from "@/lib/api/profile";

export const DERIVATIVES_RANGES = [20, 60] as const;
export type DerivativesRange = (typeof DERIVATIVES_RANGES)[number];

/** The newest `n` sessions (the API returns oldest first). */
export const lastDays = <T,>(days: T[], n: number): T[] => (n >= days.length ? days : days.slice(days.length - n));

/** Newest day on which `pick` has a value, with that value. */
export function latestValue(
  days: MarketDerivativesDay[],
  pick: (day: MarketDerivativesDay) => number | null,
): { date: string; value: number } | null {
  for (let i = days.length - 1; i >= 0; i -= 1) {
    const value = pick(days[i]);
    if (value !== null) return { date: days[i].date, value };
  }
  return null;
}

/** Position wording: facts only, no forecast. */
export const positionWord = (value: number): string => (value > 0 ? "淨多單" : value < 0 ? "淨空單" : "多空持平");

export type StrikeSide = { strike: number; contracts: number | null };
export type StrikeGroup = { key: "monthly" | "weekly"; title: string; expiry: string | null; call: StrikeSide | null; put: StrikeSide | null };

/**
 * The strikes with the largest open interest, as plain facts (monthly first, then weekly). No
 * direction is attached: the largest-OI put strike can sit far above the index, so calling
 * strikes "pressure" or "support" would mislead.
 */
export function strikeGroups(latest: MarketDerivativesLatest | null): StrikeGroup[] {
  if (!latest) return [];
  const side = (strike: number | null, contracts: number | null): StrikeSide | null =>
    strike === null ? null : { strike, contracts };
  const groups: StrikeGroup[] = [
    {
      key: "monthly",
      title: "近月",
      expiry: latest.max_call_oi_expiry ?? latest.max_put_oi_expiry,
      call: side(latest.max_call_oi_strike, latest.max_call_oi_contracts),
      put: side(latest.max_put_oi_strike, latest.max_put_oi_contracts),
    },
    {
      key: "weekly",
      title: "週選",
      expiry: latest.weekly_expiry,
      call: side(latest.weekly_max_call_oi_strike, latest.weekly_max_call_oi_contracts),
      put: side(latest.weekly_max_put_oi_strike, latest.weekly_max_put_oi_contracts),
    },
  ];
  return groups.filter((g) => g.call !== null || g.put !== null);
}

const grouped = (n: number): string => Math.round(n).toLocaleString("zh-TW");

/** 「買權 50,000（2,429 口）」; the contract count is left out when the server has none. */
export const strikeLine = (label: "買權" | "賣權", side: StrikeSide): string =>
  `${label} ${grouped(side.strike)}${side.contracts == null ? "" : `（${grouped(side.contracts)} 口）`}`;

/** Index level for reference, dated when it is not the derivatives date. */
export function indexReference(latest: MarketDerivativesLatest | null): string | null {
  // `== null`: an API one release behind sends no such field at all.
  if (!latest || latest.index_close == null) return null;
  const date = latest.index_close_date && latest.index_close_date !== latest.date ? `（${latest.index_close_date}）` : "";
  return `加權指數收盤 ${latest.index_close.toLocaleString("zh-TW", { maximumFractionDigits: 2 })}${date}`;
}

/** Put/Call tile when there is no ratio: still back-filling if other derivatives data exists. */
export const pcEmptyNote = (hasOtherData: boolean): string => (hasOtherData ? "資料回補中" : "尚無資料");

/** One-line reason the whole section is empty. */
export function derivativesEmptyReason(throughDate: string | null, dayCount: number): string | null {
  if (dayCount > 0) return null;
  return throughDate
    ? "這個日期之前還沒有期交所資料。"
    : "期交所官方日報表仍在回補（約 120 個交易日），完成前無法繪製；每個交易日收盤後更新。";
}
