import { describe, expect, it } from "vitest";
import { isCommittableAsOf } from "./as-of-commit";

const TODAY = "2026-11-28";

describe("isCommittableAsOf", () => {
  it("accepts a finished date up to today", () => {
    expect(isCommittableAsOf("2026-11-27", TODAY)).toBe(true);
    expect(isCommittableAsOf(TODAY, TODAY)).toBe(true);
    expect(isCommittableAsOf("2024-02-29", TODAY)).toBe(true);
  });

  it("rejects the future", () => {
    expect(isCommittableAsOf("2026-11-29", TODAY)).toBe(false);
  });

  it("rejects a year that is still being typed", () => {
    expect(isCommittableAsOf("0002-11-28", TODAY)).toBe(false);
    expect(isCommittableAsOf("0202-11-28", TODAY)).toBe(false);
  });

  it("rejects empty, malformed and impossible dates", () => {
    expect(isCommittableAsOf("", TODAY)).toBe(false);
    expect(isCommittableAsOf("2026-1-5", TODAY)).toBe(false);
    expect(isCommittableAsOf("2026-02-30", TODAY)).toBe(false);
    expect(isCommittableAsOf("2025-02-29", TODAY)).toBe(false);
    expect(isCommittableAsOf("2026-13-01", TODAY)).toBe(false);
  });
});
