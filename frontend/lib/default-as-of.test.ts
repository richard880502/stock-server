import { describe, expect, it } from "vitest";
import { pickDefaultAsOf, shouldShowCompactQuery } from "./default-as-of";

describe("pickDefaultAsOf", () => {
  it("uses the newest market date", () => {
    expect(
      pickDefaultAsOf([{ last_date: "2026-11-24" }, { last_date: "2026-11-23" }], "2026-11-25"),
    ).toBe("2026-11-24");
  });
  it("falls back to today without data", () => {
    expect(pickDefaultAsOf([], "2026-11-25")).toBe("2026-11-25");
    expect(pickDefaultAsOf([{ last_date: null }], "2026-11-25")).toBe("2026-11-25");
  });
  it("keeps today once data for today exists", () => {
    expect(pickDefaultAsOf([{ last_date: "2026-11-25" }], "2026-11-25")).toBe("2026-11-25");
  });
  it("ignores dates in the future", () => {
    expect(
      pickDefaultAsOf([{ last_date: "2026-12-01" }, { last_date: "2026-11-20" }], "2026-11-25"),
    ).toBe("2026-11-20");
  });
});

describe("shouldShowCompactQuery", () => {
  it("shows after scrolling past the threshold and hides near the top", () => {
    expect(shouldShowCompactQuery(0, false)).toBe(false);
    expect(shouldShowCompactQuery(100, false)).toBe(false);
    expect(shouldShowCompactQuery(200, false)).toBe(true);
    expect(shouldShowCompactQuery(100, true)).toBe(true);
    expect(shouldShowCompactQuery(10, true)).toBe(false);
  });
});
