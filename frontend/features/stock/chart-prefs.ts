import { MA_PERIODS, type MaPeriod, RANGE_PRESETS, type RangeKey } from "@/components/charts/chart-presets";

/** Stored value of the moving-average toggles when the viewer never changed them. */
export const DEFAULT_MA = "5,20,60";

/** A stored range key, or the card's default when nothing valid is stored. */
export function parseRange(stored: string | null, fallback: RangeKey): RangeKey {
  return RANGE_PRESETS.some((p) => p.key === stored) ? (stored as RangeKey) : fallback;
}

/** Stored "5,20,60"-style list → known periods only, in the stored order. "" means all off. */
export function parseMa(stored: string): MaPeriod[] {
  return stored
    .split(",")
    .filter(Boolean)
    .map(Number)
    .filter((n): n is MaPeriod => (MA_PERIODS as readonly number[]).includes(n));
}

/** Toggle one period, keeping the list sorted so the stored value is stable. */
export function toggleMa(current: MaPeriod[], period: MaPeriod): MaPeriod[] {
  return current.includes(period)
    ? current.filter((p) => p !== period)
    : [...current, period].sort((a, b) => a - b);
}
