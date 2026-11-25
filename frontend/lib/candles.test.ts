import { describe, expect, it } from "vitest";
import { type Candle, historyStart, mergeLive, movingAverage, visibleWindow, wantsLive } from "./candles";

const candle = (time: string, close: number): Candle => ({ time, open: close, high: close, low: close, close, volume: 1000 });
const series = [candle("2026-11-02", 10), candle("2026-11-03", 20), candle("2026-11-04", 30), candle("2026-11-05", 40)];

describe("movingAverage", () => {
  it("starts at the period-th candle and slides the window", () => {
    expect(movingAverage(series, 2)).toEqual([
      { time: "2026-11-03", value: 15 },
      { time: "2026-11-04", value: 25 },
      { time: "2026-11-05", value: 35 },
    ]);
  });

  it("is empty when there are fewer candles than the period", () => {
    expect(movingAverage(series, 5)).toEqual([]);
  });
});

describe("mergeLive", () => {
  it("replaces the last candle for the same session", () => {
    const live = candle("2026-11-05", 41);
    const merged = mergeLive(series, live);
    expect(merged).toHaveLength(4);
    expect(merged[3]).toBe(live);
    expect(series[3].close).toBe(40); // input untouched
  });

  it("appends a newer session", () => {
    expect(mergeLive(series, candle("2026-11-06", 50)).map((c) => c.time)).toEqual([
      "2026-11-02",
      "2026-11-03",
      "2026-11-04",
      "2026-11-05",
      "2026-11-06",
    ]);
  });

  it("ignores an update older than the loaded history and a missing update", () => {
    expect(mergeLive(series, candle("2026-11-01", 5))).toBe(series);
    expect(mergeLive(series, null)).toBe(series);
    expect(mergeLive([], candle("2026-11-06", 50))).toEqual([]);
  });
});

describe("historyStart", () => {
  it("goes back three years from the as-of date", () => {
    expect(historyStart("2026-11-25")).toBe("2023-11-25");
  });

  it("uses today when there is no as-of date", () => {
    expect(historyStart(undefined, 1, new Date("2026-02-10T12:00:00Z"))).toBe("2025-02-10");
  });
});

describe("visibleWindow", () => {
  it("shows the last N candles with room on the right", () => {
    expect(visibleWindow(500, 126)).toEqual({ from: 374, to: 503 });
  });

  it("shows everything for 'all' or when history is shorter than the preset", () => {
    expect(visibleWindow(100, Infinity)).toEqual({ from: 0, to: 103 });
    expect(visibleWindow(50, 126)).toEqual({ from: 0, to: 53 });
  });
});

describe("wantsLive", () => {
  it("streams only when the chart ends today", () => {
    expect(wantsLive(undefined, "2026-11-25")).toBe(true);
    expect(wantsLive("2026-11-25", "2026-11-25")).toBe(true);
    expect(wantsLive("2026-11-20", "2026-11-25")).toBe(false);
  });
});
