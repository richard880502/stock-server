import { describe, expect, it } from "vitest";
import { isStaleQuote, referencePrice } from "./rail-stale";

const quote = (received_at: string) => ({
  symbol: "2330.TW",
  price: 100,
  change_pct: 0,
  total_volume: 1,
  trade_time: received_at,
  received_at,
});

describe("isStaleQuote", () => {
  const now = Date.parse("2026-11-26T01:00:40Z");
  it("is fresh within 30s while live", () => {
    expect(isStaleQuote(quote("2026-11-26T01:00:20Z"), "live", now)).toBe(false);
  });
  it("is stale after 30s or when not live", () => {
    expect(isStaleQuote(quote("2026-11-26T01:00:00Z"), "live", now)).toBe(true);
    expect(isStaleQuote(quote("2026-11-26T01:00:39Z"), "unavailable", now)).toBe(true);
  });
});

describe("referencePrice", () => {
  it("derives the previous close", () => {
    expect(referencePrice(102.5, 0.025)).toBeCloseTo(100);
    expect(referencePrice(100, null)).toBeNull();
  });
});
