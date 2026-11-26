import { describe, expect, it } from "vitest";
import { changeSinceGeneration } from "./ai-price";

describe("changeSinceGeneration", () => {
  it("returns the ratio move", () => {
    expect(changeSinceGeneration(100, 102)).toBeCloseTo(0.02);
    expect(changeSinceGeneration(100, 97.5)).toBeCloseTo(-0.025);
    expect(changeSinceGeneration(100, 100)).toBe(0);
  });
  it("is null without both prices or a positive base", () => {
    expect(changeSinceGeneration(null, 100)).toBeNull();
    expect(changeSinceGeneration(100, undefined)).toBeNull();
    expect(changeSinceGeneration(0, 100)).toBeNull();
    expect(changeSinceGeneration(Number.NaN, 100)).toBeNull();
  });
});
