import { describe, expect, it } from "vitest";
import { marketSession, taipeiClock } from "./rail-session";

// 2026-11-26 is a Thursday; Taipei is UTC+8.
const at = (iso: string) => new Date(`${iso}+08:00`);

describe("marketSession", () => {
  it("covers the weekday phases", () => {
    expect(marketSession(at("2026-11-26T08:59:59"))).toBe("pre");
    expect(marketSession(at("2026-11-26T09:00:00"))).toBe("open");
    expect(marketSession(at("2026-11-26T13:29:59"))).toBe("open");
    expect(marketSession(at("2026-11-26T13:30:00"))).toBe("post");
    expect(marketSession(at("2026-11-26T23:59:59"))).toBe("post");
  });
  it("is closed on weekends", () => {
    expect(marketSession(at("2026-11-28T10:00:00"))).toBe("closed");
    expect(marketSession(at("2026-11-29T10:00:00"))).toBe("closed");
  });
  it("uses Taipei time, not the host timezone", () => {
    // 01:00 UTC Thursday = 09:00 Taipei.
    expect(marketSession(new Date("2026-11-26T01:00:00Z"))).toBe("open");
    // Friday 17:00 UTC = Saturday 01:00 Taipei.
    expect(marketSession(new Date("2026-11-27T17:00:00Z"))).toBe("closed");
  });
});

describe("taipeiClock", () => {
  it("formats HH:MM:SS with midnight as 00", () => {
    expect(taipeiClock(new Date("2026-11-26T16:00:05Z"))).toBe("00:00:05");
    expect(taipeiClock(at("2026-11-26T13:05:09"))).toBe("13:05:09");
  });
});
