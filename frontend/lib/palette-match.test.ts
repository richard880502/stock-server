import { describe, expect, it } from "vitest";
import { looksLikeSymbol, matchesQuery } from "./palette-match";

describe("looksLikeSymbol", () => {
  it.each(["2330", "2330.TW", "6488.two", "^TWII", " 2317 "])("accepts %s", (text) => {
    expect(looksLikeSymbol(text)).toBe(true);
  });

  it.each(["", "2", "台積電", "回測", "2330 TW", "a/b"])("rejects %s", (text) => {
    expect(looksLikeSymbol(text)).toBe(false);
  });
});

describe("matchesQuery", () => {
  it("matches substrings case-insensitively and everything for an empty query", () => {
    expect(matchesQuery("回測 2330.TW", "回測")).toBe(true);
    expect(matchesQuery("Switch theme", "THEME")).toBe(true);
    expect(matchesQuery("市場深入", "")).toBe(true);
    expect(matchesQuery("市場深入", "自選")).toBe(false);
  });
});
