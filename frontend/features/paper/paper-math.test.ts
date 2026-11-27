import { describe, expect, it } from "vitest";
import {
  ACCOUNT_MAIN,
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
