/** Pure helpers behind the candle chart and its data hook (no DOM, no chart library). */

/** One candle on the chart. `time` is a trading date "YYYY-MM-DD" for daily bars. */
export type Candle = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

/** Simple moving average of closes; the first point is the `period`-th candle. */
export function movingAverage(candles: Candle[], period: number): { time: string; value: number }[] {
  const out: { time: string; value: number }[] = [];
  let sum = 0;
  candles.forEach((candle, index) => {
    sum += candle.close;
    if (index >= period) sum -= candles[index - period].close;
    if (index >= period - 1) out.push({ time: candle.time, value: sum / period });
  });
  return out;
}

/**
 * Apply a live update: replace the last candle when it is the same session, append it
 * when it is a newer session, and ignore anything older than the loaded history.
 */
export function mergeLive(candles: Candle[], live: Candle | null): Candle[] {
  if (!live || candles.length === 0) return candles;
  const last = candles[candles.length - 1];
  if (live.time === last.time) return [...candles.slice(0, -1), live];
  if (live.time > last.time) return [...candles, live];
  return candles;
}

/** First date of the history window: `years` before `asOf` (or before today). */
export function historyStart(asOf: string | undefined, years = 3, today: Date = new Date()): string {
  const end = asOf ? new Date(`${asOf}T00:00:00Z`) : new Date(today);
  end.setUTCFullYear(end.getUTCFullYear() - years);
  return end.toISOString().slice(0, 10);
}

/** Logical range showing the last `bars` candles plus a little room on the right. */
export function visibleWindow(count: number, bars: number, rightOffset = 4): { from: number; to: number } {
  const from = Number.isFinite(bars) ? Math.max(0, count - bars) : 0;
  return { from, to: count - 1 + rightOffset };
}

/** Live updates only make sense when the chart ends today (or has no as-of date). */
export function wantsLive(asOf: string | undefined, today: string): boolean {
  return !asOf || asOf >= today;
}
