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

export type StrikeFact = { key: string; label: string; role: string; strike: number; expiry: string | null };

/** The largest-open-interest strikes as labelled facts (monthly first, then weekly). */
export function strikeFacts(latest: MarketDerivativesLatest | null): StrikeFact[] {
  if (!latest) return [];
  const rows: StrikeFact[] = [];
  const push = (key: string, label: string, role: string, strike: number | null, expiry: string | null) => {
    if (strike !== null) rows.push({ key, label, role, strike, expiry });
  };
  push("call", "買權未平倉最大履約價", "上方壓力參考", latest.max_call_oi_strike, latest.max_call_oi_expiry);
  push("put", "賣權未平倉最大履約價", "下方支撐參考", latest.max_put_oi_strike, latest.max_put_oi_expiry);
  push("wcall", "週選買權最大履約價", "上方壓力參考", latest.weekly_max_call_oi_strike, latest.weekly_expiry);
  push("wput", "週選賣權最大履約價", "下方支撐參考", latest.weekly_max_put_oi_strike, latest.weekly_expiry);
  return rows;
}

/** One-line reason the whole section is empty. */
export function derivativesEmptyReason(throughDate: string | null, dayCount: number): string | null {
  if (dayCount > 0) return null;
  return throughDate
    ? "這個日期之前還沒有期交所資料。"
    : "期交所官方日報表仍在回補（約 120 個交易日），完成前無法繪製；每個交易日收盤後更新。";
}
