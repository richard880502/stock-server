/** Small helpers shared by the SVG charts (no chart library). */

export type Linear = (value: number) => number;

export function linear(domain: [number, number], range: [number, number]): Linear {
  const span = domain[1] - domain[0] || 1;
  return (value) => range[0] + ((value - domain[0]) / span) * (range[1] - range[0]);
}

/** "Nice" round tick values covering [min, max]; the domain should be set to the first/last tick. */
export function niceTicks(min: number, max: number, count = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  if (min === max) {
    const pad = Math.abs(min) * 0.05 || 1;
    min -= pad;
    max += pad;
  }
  const raw = (max - min) / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const normalized = raw / magnitude;
  const step = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10) * magnitude;
  const ticks: number[] = [];
  for (let v = Math.floor(min / step) * step; v <= Math.ceil(max / step) * step + step / 2; v += step) {
    ticks.push(Number(v.toFixed(10)));
  }
  return ticks;
}

/** Compact NT$ amounts for axis labels: 120萬, 1.2億. */
export function compactTwd(value: number): string {
  const abs = Math.abs(value);
  const trim = (n: number) => n.toFixed(abs >= 1e8 ? 2 : 1).replace(/\.?0+$/, "");
  if (abs >= 1e8) return `${trim(value / 1e8)}億`;
  if (abs >= 1e4) return `${trim(value / 1e4)}萬`;
  return value.toLocaleString("zh-TW", { maximumFractionDigits: 0 });
}

/** Evenly pick up to `max` indices from 0..length-1, always including the last. */
export function sampleIndices(length: number, max: number): number[] {
  if (length <= max) return Array.from({ length }, (_, i) => i);
  const out = new Set<number>();
  for (let i = 0; i < max; i += 1) out.add(Math.round((i / (max - 1)) * (length - 1)));
  return [...out];
}
