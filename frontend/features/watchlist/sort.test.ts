import { describe, expect, it } from "vitest";
import type { WatchlistItem } from "@/lib/api/types";
import { DEFAULT_SORT, nextSort, SORT_OPTIONS, sortItems, sortToValue, valueToSort } from "./sort";

const item = (symbol: string, day: number | null, d20: number | null = null): WatchlistItem => ({
  symbol,
  name: symbol,
  market: "TW",
  added_at: "2026-11-01T00:00:00Z",
  data_through: "2026-11-24",
  close: 100,
  day_change_pct: day,
  flows_through: null,
  foreign_net_1d_lots: null,
  total_net_5d_lots: null,
  change_20d_pct: d20,
  full_analysis: "ready",
});

const items = [item("A", 1.5), item("B", null), item("C", -2), item("D", 3)];

describe("sortItems", () => {
  it("keeps the order added by default", () => {
    expect(sortItems(items, { key: "added", dir: "desc" })).toBe(items);
  });

  it("sorts high to low and low to high", () => {
    expect(sortItems(items, { key: "day_change_pct", dir: "desc" }).map((i) => i.symbol)).toEqual(["D", "A", "C", "B"]);
    expect(sortItems(items, { key: "day_change_pct", dir: "asc" }).map((i) => i.symbol)).toEqual(["C", "A", "D", "B"]);
  });

  it("always puts missing values last and treats an absent field as missing", () => {
    const sparse = [item("X", 0, null), item("Y", 0, 5), { ...item("Z", 0), change_20d_pct: undefined }];
    expect(sortItems(sparse, { key: "change_20d_pct", dir: "asc" }).map((i) => i.symbol)).toEqual(["Y", "X", "Z"]);
  });

  it("does not mutate the input", () => {
    sortItems(items, { key: "day_change_pct", dir: "desc" });
    expect(items.map((i) => i.symbol)).toEqual(["A", "B", "C", "D"]);
  });
});

describe("nextSort", () => {
  it("cycles desc → asc → order added", () => {
    let sort = nextSort({ key: "added", dir: "desc" }, "day_change_pct");
    expect(sort).toEqual({ key: "day_change_pct", dir: "desc" });
    sort = nextSort(sort, "day_change_pct");
    expect(sort).toEqual({ key: "day_change_pct", dir: "asc" });
    sort = nextSort(sort, "day_change_pct");
    expect(sort).toEqual({ key: "added", dir: "desc" });
  });

  it("starts high to low when switching columns", () => {
    expect(nextSort({ key: "day_change_pct", dir: "asc" }, "total_net_5d_lots")).toEqual({
      key: "total_net_5d_lots",
      dir: "desc",
    });
  });
});

describe("select value mapping (phone sort menu)", () => {
  it("round-trips every option", () => {
    for (const option of SORT_OPTIONS) {
      expect(valueToSort(option.value)).toEqual(option.sort);
      expect(sortToValue(option.sort)).toBe(option.value);
    }
  });

  it("offers order added plus both directions for each column", () => {
    expect(SORT_OPTIONS.map((o) => o.value)).toEqual([
      "added",
      "day_change_pct:desc",
      "day_change_pct:asc",
      "change_20d_pct:desc",
      "change_20d_pct:asc",
      "foreign_net_1d_lots:desc",
      "foreign_net_1d_lots:asc",
      "total_net_5d_lots:desc",
      "total_net_5d_lots:asc",
    ]);
  });

  it("falls back to the order added for unknown values", () => {
    expect(valueToSort("bogus")).toEqual(DEFAULT_SORT);
    expect(sortToValue({ key: "added", dir: "asc" })).toBe("added");
  });
});
