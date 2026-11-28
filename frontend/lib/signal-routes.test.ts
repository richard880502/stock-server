import { describe, expect, it } from "vitest";
import { normalizePath, routeUsesSignalData } from "./signal-routes";

describe("routeUsesSignalData", () => {
  it("loads the analysis data on stock pages and the watchlist (看盤)", () => {
    expect(routeUsesSignalData("/stock/2330.TW")).toBe(true);
    expect(routeUsesSignalData("/stock/6488.TWO/")).toBe(true);
    expect(routeUsesSignalData("/watchlist")).toBe(true);
    expect(routeUsesSignalData("/watchlist/")).toBe(true);
  });
  it("skips it on routes that never read it", () => {
    for (const path of ["/", "", null, "/market", "/screener", "/paper", "/backtest", "/forecast", "/market/", "/terminal", "/unknown"]) {
      expect(routeUsesSignalData(path)).toBe(false);
    }
  });
  it("normalizes trailing slashes", () => {
    expect(normalizePath("/market///")).toBe("/market");
    expect(normalizePath("///")).toBe("/");
  });
});
