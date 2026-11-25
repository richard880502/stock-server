import { describe, expect, it } from "vitest";
import { showEnvironmentLagNotice } from "./environment-lag";

describe("showEnvironmentLagNotice", () => {
  it("stays quiet when the environment is up to date", () => {
    expect(showEnvironmentLagNotice("2026-11-25", "2026-11-25", "2026-11-25")).toBe(false);
  });

  it("stays quiet on today while the after-market report is pending, including Monday after a weekend", () => {
    expect(showEnvironmentLagNotice("2026-11-24", "2026-11-25", "2026-11-25")).toBe(false);
    expect(showEnvironmentLagNotice("2026-11-20", "2026-11-23", "2026-11-23")).toBe(false); // Fri → Mon
  });

  it("warns when the data is more than a weekend behind today", () => {
    expect(showEnvironmentLagNotice("2026-11-02", "2026-11-25", "2026-11-25")).toBe(true);
  });

  it("warns whenever the viewer picked a date the environment does not reach", () => {
    expect(showEnvironmentLagNotice("2026-11-20", "2026-11-21", "2026-11-25")).toBe(true);
  });
});
