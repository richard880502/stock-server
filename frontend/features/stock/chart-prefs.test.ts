import { describe, expect, it } from "vitest";
import { DEFAULT_MA, parseMa, parseRange, toggleMa } from "./chart-prefs";

describe("parseRange", () => {
  it("accepts a known stored key", () => {
    expect(parseRange("1y", "6m")).toBe("1y");
  });

  it("falls back for nothing stored or a stale value", () => {
    expect(parseRange(null, "6m")).toBe("6m");
    expect(parseRange("5y", "all")).toBe("all");
  });
});

describe("parseMa", () => {
  it("reads the default and keeps only known periods", () => {
    expect(parseMa(DEFAULT_MA)).toEqual([5, 20, 60]);
    expect(parseMa("5,10,60,abc")).toEqual([5, 60]);
  });

  it("treats an empty value as every average turned off", () => {
    expect(parseMa("")).toEqual([]);
  });
});

describe("toggleMa", () => {
  it("removes a shown average and re-adds it in sorted order", () => {
    const off = toggleMa([5, 20, 60], 20);
    expect(off).toEqual([5, 60]);
    expect(toggleMa(off, 20)).toEqual([5, 20, 60]);
  });
});
