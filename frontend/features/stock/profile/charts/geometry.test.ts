import { describe, expect, it } from "vitest";
import { barRect, dualAxisTicks, extentOf, linePath, niceSteps, singleAxisTicks, slotWidths, summarizeSeries } from "./geometry";

describe("extentOf / singleAxisTicks", () => {
  it("ignores nulls and returns null when nothing is finite", () => {
    expect(extentOf([null, 3, -1], [2, null])).toEqual({ min: -1, max: 3 });
    expect(extentOf([null, null])).toBeNull();
    expect(singleAxisTicks(null, true)).toEqual([0, 1]);
  });
  it("includes zero on request and covers the data", () => {
    const ticks = singleAxisTicks({ min: 30, max: 80 }, true);
    expect(ticks[0]).toBe(0);
    expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(80);
    const free = singleAxisTicks({ min: 84.1, max: 86.9 }, false);
    expect(free[0]).toBeGreaterThan(80);
  });
});

describe("dualAxisTicks", () => {
  it("aligns zero and the row count across both axes (bars ≥ 0, line with negatives)", () => {
    const { left, right } = dualAxisTicks({ min: 0, max: 590 }, { min: -20, max: 60 });
    expect(left).toHaveLength(right.length);
    const zeroRow = left.indexOf(0);
    expect(zeroRow).toBeGreaterThan(0); // room below zero for the negative line
    expect(right[zeroRow]).toBe(0);
    expect(left[left.length - 1]).toBeGreaterThanOrEqual(590);
    expect(right[0]).toBeLessThanOrEqual(-20);
    expect(right[right.length - 1]).toBeGreaterThanOrEqual(60);
    expect(left.length).toBeLessThanOrEqual(9);
  });
  it("handles all-positive data on both sides", () => {
    const { left, right } = dualAxisTicks({ min: 30000, max: 37000 }, { min: 20000, max: 41000 });
    expect(left[0]).toBe(0);
    expect(right[0]).toBe(0);
    expect(left).toHaveLength(right.length);
  });
  it("handles missing extents and all-negative data", () => {
    const empty = dualAxisTicks(null, null);
    expect(empty.left).toHaveLength(empty.right.length);
    const negative = dualAxisTicks({ min: -50, max: -10 }, { min: -3, max: -1 });
    expect(negative.left[negative.left.length - 1]).toBeGreaterThanOrEqual(0);
    expect(negative.left[0]).toBeLessThanOrEqual(-50);
  });
  it("produces ascending nice steps", () => {
    const steps = niceSteps(590);
    expect(steps).toEqual([...steps].sort((a, b) => a - b));
    expect(steps).toContain(100);
  });
});

describe("linePath / barRect / slotWidths", () => {
  it("breaks the line at nulls instead of drawing zero", () => {
    const d = linePath([1, 2, null, 4], (i) => i * 10, (v) => 100 - v);
    expect(d).toBe("M0.0,99.0L10.0,98.0M30.0,96.0");
    expect(linePath([null, null], () => 0, () => 0)).toBe("");
  });
  it("draws bars up from zero for positives and down for negatives, never flat", () => {
    expect(barRect(50, 10, 100, 60)).toEqual({ x: 45, y: 60, width: 10, height: 40 });
    expect(barRect(50, 10, 100, 130)).toEqual({ x: 45, y: 100, width: 10, height: 30 });
    expect(barRect(50, 10, 100, 100).height).toBe(1);
  });
  it("caps bar width and survives an empty series", () => {
    expect(slotWidths(300, 3).bar).toBe(28);
    expect(slotWidths(300, 100).slot).toBe(3);
    expect(slotWidths(300, 0).slot).toBe(300);
  });
});

describe("summarizeSeries", () => {
  const fmt = (v: number) => `${v}%`;
  it("names the latest value, its date and the range", () => {
    expect(summarizeSeries("本益比", ["a", "b", "c"], [10, 30, null], fmt)).toBe("本益比：最新 30%（b），區間 10% 至 30%，共 2 筆");
  });
  it("says so when there is no data, and omits a degenerate range", () => {
    expect(summarizeSeries("x", ["a"], [null], fmt)).toBe("x：沒有可用資料");
    expect(summarizeSeries("x", ["a"], [5], fmt)).toBe("x：最新 5%（a），共 1 筆");
  });
});
