import { describe, expect, it } from "vitest";
import type { SectorRow } from "@/lib/api/types";
import { dataTimeLabel, formatClock, isTradingSession, rrgDomain, sortSectors } from "./sector-math";

function row(partial: Partial<SectorRow> & { code: string; name: string }): SectorRow {
  return {
    stock_count: 10,
    returns: { "5": { equal_pct: 0, weighted_pct: 0 } },
    turnover_value: 0,
    turnover_share: 0.1,
    turnover_share_avg20: 0.1,
    turnover_share_change: 0,
    up_count: 5,
    down_count: 5,
    flat_count: 0,
    breadth_up_pct: 50,
    institutional_net_lots: 0,
    institutional_net_value: 0,
    rrg: null,
    ...partial,
  };
}

describe("isTradingSession", () => {
  it("covers weekday 09:00-13:35 Taipei only", () => {
    expect(isTradingSession(new Date("2026-11-02T01:00:00Z"))).toBe(true); // Mon 09:00
    expect(isTradingSession(new Date("2026-11-02T05:35:00Z"))).toBe(true); // Mon 13:35
    expect(isTradingSession(new Date("2026-11-02T05:36:00Z"))).toBe(false);
    expect(isTradingSession(new Date("2026-11-02T00:59:00Z"))).toBe(false);
    expect(isTradingSession(new Date("2026-11-01T03:00:00Z"))).toBe(false); // Sunday
    expect(isTradingSession(new Date("2026-10-31T03:00:00Z"))).toBe(false); // Saturday
  });
});

describe("sortSectors", () => {
  const rows = [
    row({ code: "a", name: "甲", turnover_share: 0.3, institutional_net_value: null }),
    row({ code: "b", name: "乙", turnover_share: 0.1, institutional_net_value: 5 }),
    row({ code: "c", name: "丙", turnover_share: 0.2, institutional_net_value: -5 }),
  ];

  it("sorts numerically in both directions", () => {
    expect(sortSectors(rows, "share", "desc", 5).map((r) => r.code)).toEqual(["a", "c", "b"]);
    expect(sortSectors(rows, "share", "asc", 5).map((r) => r.code)).toEqual(["b", "c", "a"]);
  });

  it("keeps missing values last in either direction", () => {
    expect(sortSectors(rows, "institutional", "desc", 5).map((r) => r.code)).toEqual(["b", "c", "a"]);
    expect(sortSectors(rows, "institutional", "asc", 5).map((r) => r.code)).toEqual(["c", "b", "a"]);
  });
});

describe("rrgDomain", () => {
  it("is symmetric around 100 and never narrower than the minimum", () => {
    expect(rrgDomain([], 1).half).toBeGreaterThanOrEqual(1);
    const wide = row({
      code: "x",
      name: "x",
      rrg: {
        rs_ratio: 103,
        rs_momentum: 99,
        quadrant: "weakening",
        trail: [{ rs_ratio: 98, rs_momentum: 96 }],
      },
    });
    expect(rrgDomain([wide], 1).half).toBeGreaterThanOrEqual(4);
  });
});

describe("labels", () => {
  it("formats Taipei clock time", () => {
    expect(formatClock("2026-11-02T02:30:00Z")).toBe("10:30");
  });

  it("labels close vs intraday data", () => {
    const base = {
      mode: "close",
      data_through: "2026-10-30",
      data_time: "2026-10-30T10:00:00Z",
    } as const;
    expect(dataTimeLabel({ ...base } as never)).toBe("10/30 收盤");
    expect(dataTimeLabel({ ...base, mode: "intraday", data_time: "2026-11-02T02:30:00Z" } as never)).toBe(
      "盤中 10:30",
    );
  });
});
