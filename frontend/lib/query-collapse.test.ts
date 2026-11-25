import { describe, expect, it } from "vitest";
import { shouldShowCompactQuery } from "./query-collapse";

describe("shouldShowCompactQuery", () => {
  it("shows after scrolling past the threshold and hides near the top", () => {
    expect(shouldShowCompactQuery(0, false)).toBe(false);
    expect(shouldShowCompactQuery(100, false)).toBe(false);
    expect(shouldShowCompactQuery(200, false)).toBe(true);
    expect(shouldShowCompactQuery(100, true)).toBe(true);
    expect(shouldShowCompactQuery(10, true)).toBe(false);
  });
});
