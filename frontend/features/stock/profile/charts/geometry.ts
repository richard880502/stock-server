/** Pure geometry for the profile charts: domains, aligned dual axes, paths, summaries. */
import { niceTicks } from "@/components/charts/scale";

export type Extent = { min: number; max: number };

/** Min / max of the finite numbers in the lists, or null when there are none. */
export function extentOf(...lists: (number | null | undefined)[][]): Extent | null {
  let min = Infinity;
  let max = -Infinity;
  for (const list of lists) {
    for (const value of list) {
      if (value === null || value === undefined || !Number.isFinite(value)) continue;
      if (value < min) min = value;
      if (value > max) max = value;
    }
  }
  return min === Infinity ? null : { min, max };
}

/** Axis ticks covering the data (zero included on request); the first / last tick are the domain. */
export function singleAxisTicks(extent: Extent | null, includeZero: boolean): number[] {
  if (!extent) return [0, 1];
  const min = includeZero ? Math.min(0, extent.min) : extent.min;
  const max = includeZero ? Math.max(0, extent.max) : extent.max;
  return niceTicks(min, max, 4);
}

const MANTISSAS = [1, 2, 2.5, 5, 10];

/** Ascending "nice" steps (1, 2, 2.5, 5 × 10^k) around a span, from span/10 up to 2×span. */
export function niceSteps(span: number): number[] {
  const safe = span > 0 && Number.isFinite(span) ? span : 1;
  const low = Math.floor(Math.log10(safe / 10));
  const high = Math.ceil(Math.log10(safe * 2));
  const steps: number[] = [];
  for (let exp = low; exp <= high; exp += 1) {
    for (const m of MANTISSAS) {
      const step = Number((m * 10 ** exp).toPrecision(12));
      if (step >= safe / 10 && step <= safe * 2 && !steps.includes(step)) steps.push(step);
    }
  }
  return steps.sort((a, b) => a - b);
}

export type DualAxis = { left: number[]; right: number[] };

/**
 * Tick sets for a left and a right axis that share one grid and one zero line: both have the
 * same number of intervals and zero sits on the same row. Picks the pair of nice steps that
 * needs the fewest rows (≤ 8) and wastes the least range. Zero is always part of both axes.
 */
export function dualAxisTicks(left: Extent | null, right: Extent | null): DualAxis {
  const a = left ?? { min: 0, max: 1 };
  const b = right ?? { min: 0, max: 1 };
  const aMin = Math.min(0, a.min);
  const aMax = Math.max(0, a.max);
  const bMin = Math.min(0, b.min);
  const bMax = Math.max(0, b.max);
  const aSpan = aMax - aMin || 1;
  const bSpan = bMax - bMin || 1;

  let best: { neg: number; pos: number; sa: number; sb: number; score: number } | null = null;
  for (const sa of niceSteps(aSpan)) {
    for (const sb of niceSteps(bSpan)) {
      const neg = Math.max(Math.ceil(-aMin / sa - 1e-9), Math.ceil(-bMin / sb - 1e-9));
      const pos = Math.max(Math.ceil(aMax / sa - 1e-9), Math.ceil(bMax / sb - 1e-9), 1);
      const rows = neg + pos;
      if (rows > 8 || rows < 2) continue;
      const waste = (rows * sa) / aSpan + (rows * sb) / bSpan;
      const score = rows * 10 + waste;
      if (!best || score < best.score) best = { neg, pos, sa, sb, score };
    }
  }
  if (!best) {
    const sa = aSpan / 4;
    const sb = bSpan / 4;
    best = { neg: aMin < 0 || bMin < 0 ? 1 : 0, pos: 3, sa, sb, score: 0 };
  }
  const build = (step: number) => {
    const ticks: number[] = [];
    for (let i = -best.neg; i <= best.pos; i += 1) ticks.push(Number((i * step).toPrecision(12)));
    return ticks;
  };
  return { left: build(best.sa), right: build(best.sb) };
}

/** SVG path through the values; null breaks the line instead of drawing a zero. */
export function linePath(
  values: (number | null)[],
  x: (index: number) => number,
  y: (value: number) => number,
): string {
  let d = "";
  let pen = false;
  values.forEach((value, i) => {
    if (value === null || !Number.isFinite(value)) {
      pen = false;
      return;
    }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(value).toFixed(1)}`;
    pen = true;
  });
  return d;
}

/** Rectangle of a bar from the zero line to the value, at least `minHeight` tall. */
export function barRect(
  center: number,
  width: number,
  yZero: number,
  yValue: number,
  minHeight = 1,
): { x: number; y: number; width: number; height: number } {
  const height = Math.max(minHeight, Math.abs(yValue - yZero));
  const top = yValue <= yZero ? yZero - height : yZero;
  return { x: center - width / 2, y: top, width, height };
}

/** Horizontal slot width and bar width for `count` categories across a plot of `span` pixels. */
export function slotWidths(span: number, count: number, fill = 0.7): { slot: number; bar: number } {
  const slot = count > 0 ? span / count : span;
  return { slot, bar: Math.max(1.5, Math.min(slot * fill, 28)) };
}

/** Plain-language summary for the chart's accessible name: latest value, date and range. */
export function summarizeSeries(
  title: string,
  dates: string[],
  values: (number | null)[],
  format: (value: number) => string,
): string {
  let lastIndex = -1;
  for (let i = values.length - 1; i >= 0; i -= 1) {
    if (values[i] !== null && Number.isFinite(values[i] as number)) {
      lastIndex = i;
      break;
    }
  }
  const extent = extentOf(values);
  if (lastIndex < 0 || !extent) return `${title}：沒有可用資料`;
  const count = values.filter((v) => v !== null).length;
  const range = extent.min === extent.max ? "" : `，區間 ${format(extent.min)} 至 ${format(extent.max)}`;
  return `${title}：最新 ${format(values[lastIndex] as number)}（${dates[lastIndex]}）${range}，共 ${count} 筆`;
}
