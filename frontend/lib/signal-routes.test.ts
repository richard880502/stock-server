import { describe, expect, it } from "vitest";
import { normalizePath, routeUsesSignalData } from "./signal-routes";

describe("routeUsesSignalData", () => {
  it("loads the analysis data on the stock view and the watchlist (看盤)", () => {
    expect(routeUsesSignalData("/")).toBe(true);
    expect(routeUsesSignalData("/watchlist")).toBe(true);
    expect(routeUsesSignalData("/watchlist/")).toBe(true);
    expect(routeUsesSignalData("")).toBe(true);
    expect(routeUsesSignalData(null)).toBe(true);
  });
  it("skips it on routes that never read it", () => {
    for (const path of ["/market", "/screener", "/backtest", "/forecast", "/market/", "/terminal", "/unknown"]) {
      expect(routeUsesSignalData(path)).toBe(false);
    }
  });
  it("normalizes trailing slashes", () => {
    expect(normalizePath("/market///")).toBe("/market");
    expect(normalizePath("///")).toBe("/");
  });
});
