import { describe, expect, it } from "vitest";
import { quoteFromLatest } from "./live-quotes";

describe("quoteFromLatest", () => {
  it("turns the server's latest price into a quote with a ratio change", () => {
    const quote = quoteFromLatest({
      symbol: "2330.tw",
      latest_price: 1081,
      latest_change_pct: 1.98,
      latest_at: "2026-11-26T10:30:00+08:00",
    });
    expect(quote?.symbol).toBe("2330.TW");
    expect(quote?.price).toBe(1081);
    expect(quote?.change_pct).toBeCloseTo(0.0198);
    expect(quote?.trade_time).toBe("2026-11-26T10:30:00+08:00");
  });

  it("is absent when the official close is the newest price", () => {
    expect(quoteFromLatest({ symbol: "2330.TW", latest_price: null, latest_at: null })).toBeUndefined();
    expect(quoteFromLatest({ symbol: "2330.TW", latest_price: 1081 })).toBeUndefined();
  });
});
