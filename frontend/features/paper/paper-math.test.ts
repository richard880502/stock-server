import { describe, expect, it } from "vitest";
import type { AccountDetail } from "@/lib/api/paper";
import PARITY from "./live-parity.fixture.json";
import {
  ACCOUNT_MAIN,
  DETAIL_AFTER_DEPOSIT,
  DETAIL_MAIN,
  EQUITY_POINTS,
  POSITION_LOSS_DELAYED,
  POSITION_WIN,
  RULES,
} from "./paper-fixtures";
import {
  DEFAULT_TICK_BANDS,
  applyLivePrices,
  buildOrderBody,
  discountLabel,
  estimateCommission,
  formatSignedMoney,
  formatSignedPercent,
  isOnTickGrid,
  maxBuyLots,
  maxSellLots,
  parseDeepLink,
  parseLots,
  parsePrice,
  rebaseBenchmark,
  snapToTick,
  stepPrice,
  tickAt,
  tickBandsFrom,
  toneClass,
  wanLabel,
} from "./paper-math";

const params = (query: string) => new URLSearchParams(query);

describe("tick table", () => {
  it("reads ticks per band, lower bound inclusive", () => {
    expect(tickAt(9.99)).toBe(0.01);
    expect(tickAt(10)).toBe(0.05);
    expect(tickAt(49.95)).toBe(0.05);
    expect(tickAt(50)).toBe(0.1);
    expect(tickAt(100)).toBe(0.5);
    expect(tickAt(500)).toBe(1);
    expect(tickAt(999)).toBe(1);
    expect(tickAt(1000)).toBe(5);
    expect(tickAt(2175)).toBe(5);
  });

  it("prefers bands from the rules payload and ignores malformed ones", () => {
    expect(tickBandsFrom(RULES)).toEqual(RULES.tick_bands);
    expect(tickBandsFrom(null)).toBe(DEFAULT_TICK_BANDS);
    expect(tickBandsFrom({ tick_bands: [{ min: 0, max: null, tick: 0 }] })).toBe(DEFAULT_TICK_BANDS);
    const custom = [{ min: 0, max: null, tick: 0.5 }];
    expect(tickAt(20, tickBandsFrom({ tick_bands: custom }))).toBe(0.5);
  });

  it("detects the grid and snaps to the nearest tick", () => {
    expect(isOnTickGrid(1085)).toBe(true);
    expect(isOnTickGrid(1087)).toBe(false);
    expect(isOnTickGrid(49.95)).toBe(true);
    expect(isOnTickGrid(49.97)).toBe(false);
    expect(snapToTick(1087)).toBe(1085);
    expect(snapToTick(1088)).toBe(1090);
    expect(snapToTick(23.42)).toBe(23.4);
    expect(snapToTick(0.001)).toBe(0.01);
  });
});

describe("stepPrice", () => {
  it("moves by one tick inside a band without float drift", () => {
    expect(stepPrice(20.1, 1)).toBe(20.15);
    expect(stepPrice(20.15, 1)).toBe(20.2);
    expect(stepPrice(9.99, -1)).toBe(9.98);
    expect(stepPrice(1090, 1)).toBe(1095);
    expect(stepPrice(1090, -1)).toBe(1085);
  });

  it("does not clamp when the symbol has no daily limit (null limits)", () => {
    expect(stepPrice(150, 1, undefined, { down: null, up: null })).toBe(150.5);
    expect(stepPrice(150, -1, undefined, { down: null, up: null })).toBe(149.5);
    expect(stepPrice(150, 1, undefined, { down: 135, up: 150 })).toBe(150); // still clamped when known
  });

  it("uses the new band's tick when crossing a boundary", () => {
    expect(stepPrice(9.99, 1)).toBe(10);
    expect(stepPrice(10, 1)).toBe(10.05);
    expect(stepPrice(10, -1)).toBe(9.99);
    expect(stepPrice(50, -1)).toBe(49.95);
    expect(stepPrice(49.95, 1)).toBe(50);
    expect(stepPrice(100, -1)).toBe(99.9);
    expect(stepPrice(99.9, 1)).toBe(100);
    expect(stepPrice(100, 1)).toBe(100.5);
    expect(stepPrice(500, -1)).toBe(499.5);
    expect(stepPrice(1000, -1)).toBe(999);
    expect(stepPrice(999, 1)).toBe(1000);
  });

  it("snaps an off-grid price onto the grid in the stepping direction", () => {
    expect(stepPrice(1087, 1)).toBe(1090);
    expect(stepPrice(1087, -1)).toBe(1085);
  });

  it("clamps to the day's limits and to one tick above zero", () => {
    expect(stepPrice(1175, 1, DEFAULT_TICK_BANDS, { up: 1175, down: 965 })).toBe(1175);
    expect(stepPrice(965, -1, DEFAULT_TICK_BANDS, { up: 1175, down: 965 })).toBe(965);
    expect(stepPrice(0.01, -1)).toBe(0.01);
  });
});

describe("parsing", () => {
  it("parses prices and lots strictly", () => {
    expect(parsePrice("1,085.5")).toBe(1085.5);
    expect(parsePrice("")).toBeNull();
    expect(parsePrice("abc")).toBeNull();
    expect(parsePrice("0")).toBeNull();
    expect(parseLots("5")).toBe(5);
    expect(parseLots("0")).toBeNull();
    expect(parseLots("1.5")).toBeNull();
    expect(parseLots("-2")).toBeNull();
    expect(parseLots("")).toBeNull();
  });
});

describe("sizing", () => {
  it("applies the minimum commission to small orders", () => {
    expect(estimateCommission(1_000, 1)).toBe(20);
    expect(estimateCommission(1_075_000, 1)).toBe(1532);
    expect(estimateCommission(1_075_000, 0.28)).toBe(429);
  });

  it("never lets max buyable lots cost more than the cash", () => {
    const cash = 700_000;
    const lots = maxBuyLots({ availableCash: cash, price: 100 });
    expect(lots).toBe(6);
    const amount = lots * 1000 * 100;
    expect(amount + estimateCommission(amount, 1)).toBeLessThanOrEqual(cash);
    expect((lots + 1) * 100_000 + estimateCommission((lots + 1) * 100_000, 1)).toBeGreaterThan(cash);
  });

  it("accounts for the commission when cash is just enough for the stock", () => {
    expect(maxBuyLots({ availableCash: 100_000, price: 100 })).toBe(0);
    expect(maxBuyLots({ availableCash: 100_143, price: 100 })).toBe(1);
    expect(maxBuyLots({ availableCash: 100_142, price: 100 })).toBe(0);
  });

  it("a discount buys at least as many lots", () => {
    const full = maxBuyLots({ availableCash: 1_000_000, price: 99.5, discount: 1 });
    const cheap = maxBuyLots({ availableCash: 1_000_000, price: 99.5, discount: 0.28 });
    expect(cheap).toBeGreaterThanOrEqual(full);
  });

  it("returns 0 for unusable inputs", () => {
    expect(maxBuyLots({ availableCash: 0, price: 100 })).toBe(0);
    expect(maxBuyLots({ availableCash: 100_000, price: 0 })).toBe(0);
    expect(maxSellLots(null)).toBe(0);
    expect(maxSellLots(POSITION_WIN)).toBe(1);
  });
});

describe("applyLivePrices", () => {
  it("reprices positions and carries the delta into the account totals", () => {
    const quotes = new Map([["2330.TW", { price: 1100, change_pct: 0.0243 }]]);
    const live = applyLivePrices(DETAIL_MAIN, quotes);
    const win = live.positions.find((p) => p.symbol === "2330.TW")!;
    expect(win.price).toBe(1100);
    expect(win.market_value).toBe(2_200_000);
    expect(win.unrealized_pnl).toBe(2_200_000 - 2_150_500);
    expect(win.day_change_pct).toBeCloseTo(2.43, 5);
    expect(live.equity).toBe(DETAIL_MAIN.equity + 20_000);
    expect(live.total_pnl).toBe(DETAIL_MAIN.total_pnl + 20_000);
    expect(live.day_pnl).toBe(DETAIL_MAIN.day_pnl + 20_000);
    expect(live.total_return_pct).toBeCloseTo(((DETAIL_MAIN.total_pnl + 20_000) / 1_000_000) * 100, 8);
    expect(live.positions.reduce((s, p) => s + p.weight_pct, 0)).toBeGreaterThan(0);
  });

  it("leaves delayed positions on the server price and does not mutate input", () => {
    const quotes = new Map([["6488.TWO", { price: 500, change_pct: 0.2 }]]);
    const live = applyLivePrices(DETAIL_MAIN, quotes);
    expect(live.positions[1].price).toBe(POSITION_LOSS_DELAYED.price);
    expect(live.equity).toBe(ACCOUNT_MAIN.equity);
    expect(DETAIL_MAIN.positions[0].price).toBe(1090);
  });

  it("is the identity on prices without quotes", () => {
    const live = applyLivePrices(DETAIL_MAIN, new Map());
    expect(live.equity).toBe(DETAIL_MAIN.equity);
    expect(live.positions.map((p) => p.price)).toEqual([1090, 398]);
  });
});

describe("applyLivePrices with transfers", () => {
  it("moves only the open window's time-weighted factor, not the deposit", () => {
    // previous close 1,000,000; +1,000,000 deposited today; the market made +0.5% so far
    const quotes = new Map([["2330.TW", { price: 1100, change_pct: 0.0243 }]]);
    const live = applyLivePrices(DETAIL_AFTER_DEPOSIT, quotes); // +20,000 on 2,000 shares
    expect(live.equity).toBe(2_025_000);
    expect(live.total_pnl).toBe(25_000);
    expect(live.day_pnl).toBe(25_000);
    // day: 25,000 / (previous close 1,000,000 + the deposit at the start of the window)
    // = 25,000 / 2,000,000 = 1.25%
    expect(live.day_return_pct).toBeCloseTo(1.25, 8);
    // total: factor 1.0025 (= 2,005,000 / 2,000,000) x (2,025,000 / 2,005,000) = 1.0125
    expect(live.total_return_pct).toBeCloseTo(1.25, 8);
  });

  it("keeps the plain ratios on an API that does not send the new fields", () => {
    const live = applyLivePrices(DETAIL_MAIN, new Map([["2330.TW", { price: 1100, change_pct: 0.0243 }]]));
    expect(live.total_return_pct).toBeCloseTo(((DETAIL_MAIN.total_pnl + 20_000) / 1_000_000) * 100, 8);
  });
});

describe("applyLivePrices matches the server in every phase of the day", () => {
  // Written by tests/test_paper_transfer_windows.py from the real service: the server's detail at
  // one price, and its figures at another. Re-pricing the first must land on the second.
  type Case = { detail: AccountDetail; price: number; expected: Record<string, number> };
  const cases = PARITY as unknown as Record<string, Case>;

  it.each(["pre_open", "in_session", "after_close", "weekend"])("%s", (phase) => {
    const { detail, price, expected } = cases[phase];
    // the stream's quote is what triggers re-pricing; the fixture's position is not delayed
    expect(detail.positions.every((p) => !p.price_delayed)).toBe(true);
    const live = applyLivePrices(detail, new Map([[detail.positions[0].symbol, { price, change_pct: null }]]));
    expect(live.equity).toBe(expected.equity);
    expect(live.total_pnl).toBe(expected.total_pnl);
    expect(live.day_pnl).toBe(expected.day_pnl);
    // the server rounds to 0.01 both before and after; the client works from the rounded figure
    expect(Math.abs(live.day_return_pct - expected.day_return_pct)).toBeLessThan(0.006);
    expect(Math.abs(live.total_return_pct - expected.total_return_pct)).toBeLessThan(0.011);
  });

  it("covers a post-close flow in every phase but the session", () => {
    expect(cases.pre_open.detail.post_close_transfer).not.toBe(0);
    expect(cases.after_close.detail.post_close_transfer).not.toBe(0);
    expect(cases.weekend.detail.post_close_transfer).not.toBe(0);
    expect(cases.in_session.detail.post_close_transfer).toBe(0);
    expect(cases.in_session.detail.day_net_transfer).not.toBe(0);
  });

  it("keeps a factor the server pinned to 1 at 1", () => {
    const pinned = { ...cases.weekend.detail, equity: 300_000, day_pnl: 0, post_close_transfer: 400_000 };
    const live = applyLivePrices(pinned, new Map([[pinned.positions[0].symbol, { price: 130, change_pct: null }]]));
    expect(live.total_return_pct).toBe(pinned.total_return_pct);
    expect(live.day_return_pct).toBe(pinned.day_return_pct);
  });
});

describe("rebaseBenchmark with transfers", () => {
  const point = (date: string, equity: number, close: number | null, flow?: number) => ({
    date,
    equity,
    cash: equity,
    market_value: 0,
    benchmark_close: close,
    live: false,
    ...(flow === undefined ? {} : { net_transfer: flow }),
  });

  it("is exactly the plain rebase when nothing was transferred", () => {
    const plain = rebaseBenchmark(EQUITY_POINTS);
    const zeros = rebaseBenchmark(EQUITY_POINTS.map((p) => ({ ...p, net_transfer: 0 })));
    expect(zeros.benchmark).toEqual(plain.benchmark);
    expect(plain.flows).toEqual([0, 0, 0, 0, 0]);
  });

  it("buys and sells the index at the start of each window: B_t = (B_{t-1} + F_t) x idx_t / idx_{t-1}", () => {
    // 11/23  equity 1,000,000, index 100               -> B = 1,000,000
    // 11/24  index 110 (+10%)                          -> B = 1,100,000
    // 11/25  index 110, deposit 1,100,000              -> B = (1,100,000 + 1,100,000) x 1 = 2,200,000
    // 11/26  index 121 (+10%), withdraw 200,000        -> B = (2,200,000 - 200,000) x 1.1 = 2,200,000
    // 11/27  no index close, deposit 50,000            -> null, carried
    // 11/28  index 121                                 -> B = (2,200,000 + 50,000) x 1 = 2,250,000
    const series = rebaseBenchmark([
      point("2026-11-23", 1_000_000, 100, 0),
      point("2026-11-24", 1_100_000, 110, 0),
      point("2026-11-25", 2_300_000, 110, 1_100_000),
      point("2026-11-26", 2_400_000, 121, -200_000),
      point("2026-11-27", 2_460_000, null, 50_000),
      point("2026-11-28", 2_470_000, 121, 0),
    ]);
    expect(series.flows).toEqual([0, 0, 1_100_000, -200_000, 50_000, 0]);
    const b = series.benchmark;
    expect(b[0]).toBe(1_000_000);
    expect(b[1]).toBeCloseTo(1_100_000, 4);
    expect(b[2]).toBeCloseTo(2_200_000, 4);
    expect(b[3]).toBeCloseTo(2_200_000, 4);
    expect(b[4]).toBeNull();
    expect(b[5]).toBeCloseTo(2_250_000, 4);
  });

  it("never goes below zero and starts at the first day both series exist", () => {
    const series = rebaseBenchmark([
      point("2026-11-23", 1_000_000, null, 0),
      point("2026-11-24", 1_000_000, 100, 0),
      point("2026-11-25", 10_000, 50, -1_100_000),
    ]);
    expect(series.benchmark[0]).toBeNull();
    expect(series.benchmark[1]).toBe(1_000_000);
    expect(series.benchmark[2]).toBe(0);
  });

  it("a post-close deposit on the live point earns none of that day's index move", () => {
    // Friday index +10% on 1,000,000; Saturday +1,000,000 (post_close_transfer on Friday's live
    // point, whose equity already includes it): B = 1,000,000 x 1.10 + 1,000,000 = 2,100,000
    const series = rebaseBenchmark([
      point("2026-11-26", 1_000_000, 100, 0),
      { ...point("2026-11-27", 2_100_000, 110, 0), live: true, post_close_transfer: 1_000_000 },
    ]);
    expect(series.benchmark[1]).toBeCloseTo(2_100_000, 4);
    // and the next window carries it at the start: (2,100,000 + 0) x 1.0
    const next = rebaseBenchmark([
      point("2026-11-26", 1_000_000, 100, 0),
      { ...point("2026-11-27", 2_100_000, 110, 0), post_close_transfer: 1_000_000 },
      point("2026-11-30", 2_100_000, 110, 0),
    ]);
    expect(next.benchmark[2]).toBeCloseTo(2_100_000, 4);
  });

  it("a deposit earns the day's index move, unlike an end-of-day rule", () => {
    const series = rebaseBenchmark([
      point("2026-11-23", 1_000_000, 100, 0),
      point("2026-11-24", 2_200_000, 110, 1_000_000),
    ]);
    // (1,000,000 + 1,000,000) x 1.10 = 2,200,000 (the end-of-day rule gave 2,100,000)
    expect(series.benchmark[1]).toBeCloseTo(2_200_000, 4);
  });
});

describe("rebaseBenchmark", () => {
  it("starts the benchmark at the account's first equity", () => {
    const series = rebaseBenchmark(EQUITY_POINTS);
    expect(series.hasBenchmark).toBe(true);
    expect(series.benchmark[0]).toBe(1_000_000);
    expect(series.benchmark[4]).toBeCloseTo((27_123.4 / 27_000) * 1_000_000, 4);
    expect(series.equity).toHaveLength(5);
  });

  it("rebases at the first day that has a benchmark and blanks earlier days", () => {
    const points = EQUITY_POINTS.map((p, i) => (i < 2 ? { ...p, benchmark_close: null } : p));
    const series = rebaseBenchmark(points);
    expect(series.benchmark.slice(0, 2)).toEqual([null, null]);
    expect(series.benchmark[2]).toBe(points[2].equity);
  });

  it("reports no benchmark when fewer than two points have one", () => {
    const points = EQUITY_POINTS.map((p, i) => (i === 0 ? p : { ...p, benchmark_close: null }));
    expect(rebaseBenchmark(points).hasBenchmark).toBe(false);
    expect(rebaseBenchmark([]).equity).toEqual([]);
  });
});

describe("deep link and request bodies", () => {
  it("parses symbol, side and lots", () => {
    expect(parseDeepLink(params("symbol=2330.TW&side=buy"))).toEqual({ symbol: "2330.TW", side: "buy", lots: null });
    expect(parseDeepLink(params("symbol=2330&side=SELL&lots=3"))).toEqual({ symbol: "2330.TW", side: "sell", lots: 3 });
    expect(parseDeepLink(params("symbol=6488.two"))).toEqual({ symbol: "6488.TWO", side: null, lots: null });
  });

  it("ignores garbage", () => {
    expect(parseDeepLink(params("symbol=hello&side=short&lots=-1"))).toEqual({ symbol: null, side: null, lots: null });
    expect(parseDeepLink(null)).toEqual({ symbol: null, side: null, lots: null });
  });

  it("builds limit and market bodies", () => {
    expect(buildOrderBody({ symbol: "2330.TW", side: "buy", orderType: "limit", priceText: "1,085", lotsText: "2" })).toEqual({
      symbol: "2330.TW",
      side: "buy",
      order_type: "limit",
      limit_price: 1085,
      lots: 2,
    });
    expect(buildOrderBody({ symbol: "2330", side: "sell", orderType: "market", priceText: "ignored", lotsText: "1" })).toEqual({
      symbol: "2330.TW",
      side: "sell",
      order_type: "market",
      lots: 1,
    });
  });

  it("returns null while the inputs are incomplete", () => {
    const ok = { symbol: "2330.TW", side: "buy" as const, orderType: "limit" as const, priceText: "10", lotsText: "1" };
    expect(buildOrderBody({ ...ok, symbol: null })).toBeNull();
    expect(buildOrderBody({ ...ok, lotsText: "" })).toBeNull();
    expect(buildOrderBody({ ...ok, priceText: "" })).toBeNull();
    expect(buildOrderBody({ ...ok, orderType: "market", priceText: "" })).not.toBeNull();
  });
});

describe("formatting", () => {
  it("signs money and percent with a true minus and tones them", () => {
    expect(formatSignedMoney(3200)).toBe("+3,200");
    expect(formatSignedMoney(-1200)).toBe("−1,200");
    expect(formatSignedMoney(0)).toBe("0");
    expect(formatSignedPercent(1.38)).toBe("+1.38%");
    expect(formatSignedPercent(-0.12)).toBe("−0.12%");
    expect(toneClass(5)).toBe("text-up");
    expect(toneClass(-5)).toBe("text-down");
    expect(toneClass(0)).toBe("text-muted");
    expect(toneClass(null)).toBe("text-muted");
  });

  it("labels discounts and amounts", () => {
    expect(discountLabel(1)).toBe("不打折");
    expect(discountLabel(0.6)).toBe("6 折");
    expect(discountLabel(0.28)).toBe("2.8 折");
    expect(wanLabel(500_000)).toBe("50 萬");
    expect(wanLabel(10_000_000)).toBe("1,000 萬");
  });
});
