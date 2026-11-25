import type { WatchlistItem } from "@/lib/api/types";

export type SortKey = "added" | "day_change_pct" | "change_20d_pct" | "foreign_net_1d_lots" | "total_net_5d_lots";
export type Sort = { key: SortKey; dir: "desc" | "asc" };

export const DEFAULT_SORT: Sort = { key: "added", dir: "desc" };

export function sortItems(items: WatchlistItem[], sort: Sort): WatchlistItem[] {
  if (sort.key === "added") return items;
  const key = sort.key;
  const sign = sort.dir === "desc" ? -1 : 1;
  // Missing values always sink to the bottom, whatever the direction.
  return [...items].sort((a, b) => {
    const av = a[key] ?? null;
    const bv = b[key] ?? null;
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    return sign * (av - bv);
  });
}

/** Header click cycle: high → low, then low → high, then back to the order added. */
export function nextSort(current: Sort, key: SortKey): Sort {
  if (current.key !== key) return { key, dir: "desc" };
  if (current.dir === "desc") return { key, dir: "asc" };
  return DEFAULT_SORT;
}

const COLUMN_LABELS: Record<Exclude<SortKey, "added">, string> = {
  day_change_pct: "當日漲跌",
  change_20d_pct: "20 日走勢",
  foreign_net_1d_lots: "外資 1 日",
  total_net_5d_lots: "三大法人 5 日",
};

export type SortOption = { value: string; label: string; sort: Sort };

/** Options for the card-layout sort `<select>` (the table headers are hidden there). */
export const SORT_OPTIONS: SortOption[] = [
  { value: "added", label: "加入順序", sort: DEFAULT_SORT },
  ...(Object.keys(COLUMN_LABELS) as Exclude<SortKey, "added">[]).flatMap((key): SortOption[] => [
    { value: `${key}:desc`, label: `${COLUMN_LABELS[key]} 高→低`, sort: { key, dir: "desc" } },
    { value: `${key}:asc`, label: `${COLUMN_LABELS[key]} 低→高`, sort: { key, dir: "asc" } },
  ]),
];

export function sortToValue(sort: Sort): string {
  return sort.key === "added" ? "added" : `${sort.key}:${sort.dir}`;
}

/** Unknown values fall back to the order added. */
export function valueToSort(value: string): Sort {
  return SORT_OPTIONS.find((option) => option.value === value)?.sort ?? DEFAULT_SORT;
}
