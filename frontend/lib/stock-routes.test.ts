import { describe, expect, it } from "vitest";
import { backtestHref, isStockRoute, stockHref, symbolFromParam, symbolFromPath } from "./stock-routes";

describe("stock routes", () => {
  it("builds stock links", () => {
    expect(stockHref("2330.tw")).toBe("/stock/2330.TW");
    expect(stockHref("6488.TWO", "profile")).toBe("/stock/6488.TWO#profile");
    expect(stockHref("2330.TW", "summary")).toBe("/stock/2330.TW");
    expect(backtestHref("2330.TW")).toBe("/backtest?symbol=2330.TW");
  });
  it("reads the symbol back from a path", () => {
    expect(symbolFromPath("/stock/2330.TW")).toBe("2330.TW");
    expect(symbolFromPath("/stock/6488.two/")).toBe("6488.TWO");
    expect(symbolFromPath("/stock/2330")).toBe("2330.TW");
    expect(symbolFromPath("/stock/00631L.TW")).toBe("00631L.TW");
  });
  it("rejects anything that is not one stock", () => {
    for (const path of ["/", "/market", "/stock/", "/stock/abc", "/stock/%5ETWII", "/stock/2330.TW/extra", "/stock/%E0%A4%A", null]) {
      expect(symbolFromPath(path)).toBeNull();
    }
    expect(symbolFromParam("^TWII")).toBeNull();
    expect(symbolFromParam("")).toBeNull();
  });
  it("recognizes stock routes even when the code is invalid", () => {
    expect(isStockRoute("/stock/abc")).toBe(true);
    expect(isStockRoute("/stocks")).toBe(false);
  });
});
