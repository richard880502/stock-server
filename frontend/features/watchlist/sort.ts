import type { WatchlistItem } from "@/lib/api/types";

export type SortKey = "added" | "day_change_pct" | "change_20d_pct" | "foreign_net_1d_lots" | "total_net_5d_lots";
export type Sort = { key: SortKey; dir: "desc" | "asc" };

/** Sort a watchlist copy; "added" keeps the server order. Missing values always sink to the bottom. */
export function sortItems(items: WatchlistItem[], sort: Sort): WatchlistItem[] {
  if (sort.key === "added") return items;
  const key = sort.key;
  const sign = sort.dir === "desc" ? -1 : 1;
  return [...items].sort((a, b) => {
    const av = a[key] ?? null;
    const bv = b[key] ?? null;
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    return sign * (av - bv);
  });
}

/** Header clicks cycle: high → low, then low → high, then back to the order added. */
export function nextSort(current: Sort, key: SortKey): Sort {
  if (current.key !== key) return { key, dir: "desc" };
  if (current.dir === "desc") return { key, dir: "asc" };
  return { key: "added", dir: "desc" };
}
