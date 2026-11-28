import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { today } from "@/lib/format";

const state = { asOf: "" };
vi.mock("@/lib/dashboard-context", () => ({
  useDashboard: () => ({ query: { asOf: state.asOf, benchmark: "^TWII", symbol: "2330.TW" }, refresh: () => {} }),
}));

import { AsOfControl } from "./as-of-control";
import { SECONDARY_VIEWS, VIEWS } from "./view-tabs";

describe("AsOfControl", () => {
  it("reads as today, without a reset button", () => {
    state.asOf = today();
    const html = renderToStaticMarkup(<AsOfControl />);
    expect(html).toContain("今日");
    expect(html).not.toContain("data-historical");
    expect(html).not.toContain("回到今天");
  });

  it("turns into a warning with a way back when a past date is active", () => {
    state.asOf = "2020-01-02";
    const html = renderToStaticMarkup(<AsOfControl />);
    expect(html).toContain('data-historical="true"');
    expect(html).toContain("2020-01-02");
    expect(html).toContain("回到今天");
  });
});

describe("navigation lists", () => {
  it("follows the research journey and keeps 預測 out of the primary nav", () => {
    expect(VIEWS.map((v) => v.href)).toEqual(["/", "/market", "/screener", "/watchlist", "/backtest", "/paper"]);
    expect(VIEWS.map((v) => v.label)).not.toContain("預測");
    expect(SECONDARY_VIEWS.map((v) => v.href)).toEqual(["/forecast"]);
  });
});
