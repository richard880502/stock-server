import type { SectorQuadrant, SectorRotation, SectorRow } from "@/lib/api/types";

export const SECTOR_WINDOWS = [1, 5, 20] as const;
/** The intraday collector ticks every 10 minutes, so the page asks again at the same pace. */
export const SECTOR_REFRESH_MS = 10 * 60_000;

export type SectorSortKey = "name" | "ret" | "share" | "shareChange" | "breadth" | "institutional";

export const QUADRANTS: Record<SectorQuadrant, { label: string; hint: string }> = {
  leading: { label: "領先", hint: "相對大盤強，且還在轉強" },
  weakening: { label: "轉弱", hint: "相對大盤仍強，但動能下滑" },
  lagging: { label: "落後", hint: "相對大盤弱，且動能偏弱" },
  improving: { label: "改善", hint: "相對大盤仍弱，但動能回升" },
};

const TAIPEI_PARTS = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Taipei",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** Weekday 09:00–13:35 Taipei (the last snapshot lands a few minutes after the close). */
export function isTradingSession(now: Date): boolean {
  const parts = Object.fromEntries(TAIPEI_PARTS.formatToParts(now).map((p) => [p.type, p.value]));
  if (parts.weekday === "Sat" || parts.weekday === "Sun") return false;
  const minutes = Number(parts.hour) * 60 + Number(parts.minute);
  return minutes >= 9 * 60 && minutes <= 13 * 60 + 35;
}

const CLOCK = new Intl.DateTimeFormat("zh-TW", {
  timeZone: "Asia/Taipei",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** "10:30" in Taipei time. */
export const formatClock = (iso: string) => CLOCK.format(new Date(iso));

export function sectorReturn(row: SectorRow, window: number): number | null {
  return row.returns[String(window)]?.equal_pct ?? null;
}

function sortValue(row: SectorRow, key: SectorSortKey, window: number): number | string | null {
  switch (key) {
    case "name":
      return row.name;
    case "ret":
      return sectorReturn(row, window);
    case "share":
      return row.turnover_share;
    case "shareChange":
      return row.turnover_share_change;
    case "breadth":
      return row.breadth_up_pct;
    case "institutional":
      return row.institutional_net_value;
  }
}

/** Missing values always sink to the bottom, whichever way the column is sorted. */
export function sortSectors(
  rows: SectorRow[],
  key: SectorSortKey,
  direction: "asc" | "desc",
  window: number,
): SectorRow[] {
  const sign = direction === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const left = sortValue(a, key, window);
    const right = sortValue(b, key, window);
    if (left === null && right === null) return 0;
    if (left === null) return 1;
    if (right === null) return -1;
    if (typeof left === "string" && typeof right === "string") return sign * left.localeCompare(right, "zh-TW");
    return sign * ((left as number) - (right as number));
  });
}

/** Symmetric axis range around 100 that fits every point and trail, with a minimum span. */
export function rrgDomain(rows: SectorRow[], minHalfSpan = 1): { half: number } {
  let half = minHalfSpan;
  for (const row of rows) {
    if (!row.rrg) continue;
    for (const point of [row.rrg, ...row.rrg.trail]) {
      half = Math.max(half, Math.abs(point.rs_ratio - 100), Math.abs(point.rs_momentum - 100));
    }
  }
  return { half: Math.ceil(half * 1.15 * 10) / 10 };
}

/** What the page should tell the reader about where the numbers come from. */
export function dataTimeLabel(data: SectorRotation): string {
  if (data.mode === "intraday" && data.data_time) return `盤中 ${formatClock(data.data_time)}`;
  if (data.data_through) return `${data.data_through.slice(5).replace("-", "/")} 收盤`;
  return "—";
}
