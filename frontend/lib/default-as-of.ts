import type { MarketDataStatus } from "./api/types";

/**
 * Default "資料截止日": the newest trading date any market actually has data for, never later
 * than `todayIso`. Before the exchange publishes after-market data this is yesterday's session
 * rather than today, so the first view does not open with a "data is older than your date" warning.
 * Falls back to `todayIso` when no market reports a date.
 */
export function pickDefaultAsOf(
  statuses: readonly Pick<MarketDataStatus, "last_date">[],
  todayIso: string,
): string {
  let best: string | null = null;
  for (const { last_date } of statuses) {
    if (!last_date || last_date > todayIso) continue;
    if (best === null || last_date > best) best = last_date;
  }
  return best ?? todayIso;
}

/**
 * Whether the compact query summary should be showing. Hysteresis keeps it from flickering
 * around the threshold: it appears once the full query bar has scrolled away and hides again
 * only when the user is back near the top.
 */
export function shouldShowCompactQuery(scrollY: number, wasShown: boolean): boolean {
  return wasShown ? scrollY > 24 : scrollY > 150;
}
