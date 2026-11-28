import { describe, expect, it } from "vitest";
import type { LiveQuote } from "@/lib/live-quotes";
import { bareCode, pickActiveSection, pickHeaderPrice, sectionFromHash, stockTitle } from "./model";

const quote = (over: Partial<LiveQuote> = {}): LiveQuote => ({
  symbol: "2330.TW",
  price: 1000,
  change_pct: 0.02,
  total_volume: 5000,
  trade_time: "2026-11-27T05:30:00Z",
  received_at: "2026-11-27T05:30:00Z",
  ...over,
});
const overview = {
  symbol: "2330.TW",
  data_through: "2026-11-27",
  summary: { last_close: 980, day_change_pct: -1.5 },
} as Parameters<typeof pickHeaderPrice>[0]["overview"];
const NOW = Date.parse("2026-11-27T05:30:10Z");

describe("pickHeaderPrice", () => {
  it("prefers a live quote and derives the change from the ratio", () => {
    const picked = pickHeaderPrice({ quote: quote(), status: "live", now: NOW, overview, symbol: "2330.TW" });
    expect(picked?.source).toBe("live");
    expect(picked?.price).toBe(1000);
    expect(picked?.change).toBeCloseTo(1000 - 1000 / 1.02, 6);
    expect(picked?.stale).toBe(false);
  });
  it("marks a live quote stale when the stream is down or the tick is old", () => {
    expect(pickHeaderPrice({ quote: quote(), status: "unavailable", now: NOW, overview: null, symbol: "2330.TW" })?.stale).toBe(true);
    expect(pickHeaderPrice({ quote: quote(), status: "live", now: NOW + 60_000, overview: null, symbol: "2330.TW" })?.stale).toBe(true);
  });
  it("falls back to the daily close, converting percent to a ratio", () => {
    const picked = pickHeaderPrice({ quote: undefined, status: "connecting", now: NOW, overview, symbol: "2330.TW" });
    expect(picked).toMatchObject({ source: "daily", price: 980, changePct: -0.015, asOfLabel: "2026-11-27", stale: false });
    expect(picked?.change).toBeLessThan(0);
  });
  it("never uses another symbol's overview or a zero price", () => {
    expect(pickHeaderPrice({ quote: undefined, status: "live", now: NOW, overview, symbol: "2317.TW" })).toBeNull();
    expect(pickHeaderPrice({ quote: quote({ price: 0 }), status: "live", now: NOW, overview: null, symbol: "2330.TW" })).toBeNull();
  });
  it("keeps a missing change as null", () => {
    const picked = pickHeaderPrice({ quote: quote({ change_pct: null }), status: "live", now: NOW, overview: null, symbol: "2330.TW" });
    expect(picked?.changePct).toBeNull();
    expect(picked?.change).toBeNull();
  });
});

describe("sections", () => {
  it("reads plain and profile-tab hashes", () => {
    expect(sectionFromHash("#chart")).toBe("chart");
    expect(sectionFromHash("#profile")).toBe("profile");
    expect(sectionFromHash("#profile=earnings")).toBe("profile");
    expect(sectionFromHash("#nope")).toBeNull();
    expect(sectionFromHash("")).toBeNull();
    expect(sectionFromHash(null)).toBeNull();
  });
  it("picks the last section whose top passed the line", () => {
    const tops = { summary: -300, chart: -20, evidence: 400, profile: 900, chips: 1400 };
    expect(pickActiveSection(tops, 100)).toBe("chart");
    expect(pickActiveSection({ ...tops, evidence: 90 }, 100)).toBe("evidence");
  });
  it("defaults to the first present section and to the last at the bottom", () => {
    expect(pickActiveSection({ summary: 300, chart: 800 }, 100)).toBe("summary");
    expect(pickActiveSection({ summary: -900, chart: -400, chips: 500 }, 100, true)).toBe("chips");
    expect(pickActiveSection({}, 100)).toBeNull();
  });
});

describe("titles", () => {
  it("formats code and title", () => {
    expect(bareCode("6488.TWO")).toBe("6488");
    expect(stockTitle("2330.TW", "台積電")).toBe("2330 台積電 — Quant Signal");
    expect(stockTitle("2330.TW", "")).toBe("2330 — Quant Signal");
  });
});
