import { describe, expect, it } from "vitest";
import { aggregateBars, mergeBars, type Bar } from "./intraday-data";

const bar = (time: number, overrides: Partial<Bar> = {}): Bar => ({
  time, open: 10, high: 12, low: 9, close: 11, volume: 1000,
  is_partial: false, lastTradeTime: time * 1000, ...overrides,
});

describe("intraday data", () => {
  it("retains minutes and merges ranges while choosing the latest close", () => {
    const history = [bar(300, { close: 10, lastTradeTime: 100 }), bar(360)];
    const live = [bar(300, { high: 14, low: 8, close: 13, volume: 2000, lastTradeTime: 200 }), bar(420)];
    const result = mergeBars(history, live);
    expect(result.map((item) => item.time)).toEqual([300, 360, 420]);
    expect(result[0]).toMatchObject({ open: 10, high: 14, low: 8, close: 13, volume: 2000 });
    expect(mergeBars([bar(300, { close: 12, lastTradeTime: 300 })], live)[0].close).toBe(12);
  });

  it("aggregates observed minutes into five minute buckets without filling gaps", () => {
    const bars = [bar(300), bar(420, { close: 14, volume: 2000 }), bar(600)];
    expect(aggregateBars(bars, 1)).toEqual(bars);
    expect(aggregateBars(bars, 5)).toMatchObject([
      { time: 300, open: 10, close: 14, volume: 3000 },
      { time: 600, open: 10, close: 11, volume: 1000 },
    ]);
  });
});
