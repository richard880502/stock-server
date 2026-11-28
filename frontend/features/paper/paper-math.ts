// Pure helpers for the paper-trading page: ticks, sizing, live revaluation, chart rebasing,
// deep links, request bodies and number formatting. No React, no network.
import type {
  AccountDetail,
  EquityPoint,
  OrderRequest,
  OrderSide,
  OrderStatus,
  OrderType,
  PaperRules,
  Position,
  PriceSource,
  TickBand,
} from "@/lib/api/paper";
import { directionOf, formatSigned } from "@/lib/format";
import { normalizeSymbolInput } from "@/lib/symbols";

export const BOARD_LOT = 1000;

/** Taiwan stock tick table; only a fallback until GET /paper/rules answers. */
export const DEFAULT_TICK_BANDS: TickBand[] = [
  { min: 0, max: 10, tick: 0.01 },
  { min: 10, max: 50, tick: 0.05 },
  { min: 50, max: 100, tick: 0.1 },
  { min: 100, max: 500, tick: 0.5 },
  { min: 500, max: 1000, tick: 1 },
  { min: 1000, max: null, tick: 5 },
];

const round2 = (value: number) => Math.round(value * 100) / 100;

/** Bands from the rules payload when they look valid, else the built-in table. */
export function tickBandsFrom(rules: PaperRules | null | undefined): TickBand[] {
  const bands = rules?.tick_bands;
  if (!Array.isArray(bands) || bands.length === 0) return DEFAULT_TICK_BANDS;
  const valid = bands.every(
    (band) =>
      band && typeof band.min === "number" && typeof band.tick === "number" && band.tick > 0 &&
      (band.max === null || typeof band.max === "number"),
  );
  return valid ? [...bands].sort((a, b) => a.min - b.min) : DEFAULT_TICK_BANDS;
}

/** Tick for a price: bands are [min, max). */
export function tickAt(price: number, bands: TickBand[] = DEFAULT_TICK_BANDS): number {
  for (const band of bands) if (price >= band.min && (band.max === null || price < band.max)) return band.tick;
  return bands[bands.length - 1].tick;
}

/** Tick used when moving DOWN from a price: a boundary price belongs to the band below it. */
function tickBelow(price: number, bands: TickBand[]): number {
  for (const band of bands) if (price > band.min && (band.max === null || price <= band.max)) return band.tick;
  return bands[0].tick;
}

export function isOnTickGrid(price: number, bands: TickBand[] = DEFAULT_TICK_BANDS): boolean {
  const tick = tickAt(price, bands);
  const steps = price / tick;
  return Math.abs(steps - Math.round(steps)) < 1e-6;
}

/** Nearest grid price (halves round up), at least one tick. */
export function snapToTick(price: number, bands: TickBand[] = DEFAULT_TICK_BANDS): number {
  const tick = tickAt(price, bands);
  return Math.max(tick, round2(Math.round(price / tick + 1e-9) * tick));
}

/**
 * One tick up or down from `price`, snapping off-grid input onto the grid first.
 * `limits` clamps the result (today's 漲停 / 跌停 when known).
 */
export function stepPrice(
  price: number,
  direction: 1 | -1,
  bands: TickBand[] = DEFAULT_TICK_BANDS,
  limits?: { down?: number | null; up?: number | null },
): number {
  let next: number;
  if (direction === 1) {
    const tick = tickAt(price, bands);
    next = (Math.floor(price / tick + 1e-9) + 1) * tick;
  } else {
    const tick = tickBelow(price, bands);
    next = (Math.ceil(price / tick - 1e-9) - 1) * tick;
  }
  next = round2(next);
  const floor = Math.max(limits?.down ?? 0, tickAt(0, bands));
  if (next < floor) next = floor;
  if (limits?.up != null && next > limits.up) next = limits.up;
  return next;
}

/** "2,175" / "12.5" -> number; null when empty or not a positive finite number. */
export function parsePrice(text: string): number | null {
  const cleaned = text.replace(/,/g, "").trim();
  if (!cleaned) return null;
  const value = Number(cleaned);
  return Number.isFinite(value) && value > 0 ? value : null;
}

export const formatPriceInput = (value: number) => String(round2(value));

/** Whole 張 count from text; null unless a positive integer. */
export function parseLots(text: string): number | null {
  const cleaned = text.replace(/,/g, "").trim();
  if (!/^\d+$/.test(cleaned)) return null;
  const value = Number(cleaned);
  return value >= 1 && Number.isSafeInteger(value) ? value : null;
}

/** Commission for an amount: rate x discount, with the per-order minimum (rounded up = conservative). */
export function estimateCommission(
  amount: number,
  discount: number,
  rules?: Pick<PaperRules, "commission_rate" | "commission_min"> | null,
): number {
  const rate = rules?.commission_rate ?? 0.001425;
  const min = rules?.commission_min ?? 20;
  return Math.max(min, Math.ceil(amount * rate * discount - 1e-9));
}

/**
 * Most 張 that cash can cover for a buy at `price` (limit price, or limit-up for a market
 * order, because that is what the server reserves). Conservative; the server preview is final.
 */
export function maxBuyLots(input: {
  availableCash: number;
  price: number;
  discount?: number;
  rules?: Pick<PaperRules, "commission_rate" | "commission_min"> | null;
}): number {
  const { availableCash, price, discount = 1, rules } = input;
  if (!(availableCash > 0) || !(price > 0)) return 0;
  const cost = (lots: number) => {
    const amount = lots * BOARD_LOT * price;
    return amount + estimateCommission(amount, discount, rules);
  };
  const rate = rules?.commission_rate ?? 0.001425;
  let lots = Math.floor(availableCash / (BOARD_LOT * price * (1 + rate * discount)));
  while (lots > 0 && cost(lots) > availableCash) lots -= 1;
  while (cost(lots + 1) <= availableCash) lots += 1;
  return Math.max(0, lots);
}

/** Sellable 張 of a position (settled / unreserved shares only). */
export const maxSellLots = (position: Pick<Position, "available_shares"> | null | undefined) =>
  position ? Math.floor(position.available_shares / BOARD_LOT) : 0;

/* ---- Live revaluation -------------------------------------------------------------------- */

export type QuoteLike = { price: number; change_pct: number | null };

/**
 * Re-value positions at the live stream's prices and carry the difference into the account
 * totals, so the page ticks between server polls. Delayed positions keep the server price.
 * Quote `change_pct` is a ratio; the API's `day_change_pct` is a percentage.
 */
export function applyLivePrices(detail: AccountDetail, quotes: ReadonlyMap<string, QuoteLike>): AccountDetail {
  let delta = 0;
  const positions = detail.positions.map((position): Position => {
    const quote = quotes.get(position.symbol.toUpperCase());
    if (!quote || position.price_delayed || !(quote.price > 0) || quote.price === position.price) {
      return quote && quote.change_pct !== null && !position.price_delayed
        ? { ...position, day_change_pct: quote.change_pct * 100 }
        : position;
    }
    const marketValue = Math.round(position.shares * quote.price);
    delta += marketValue - position.market_value;
    const unrealized = marketValue - position.cost_basis;
    return {
      ...position,
      price: quote.price,
      market_value: marketValue,
      unrealized_pnl: unrealized,
      unrealized_pct: position.cost_basis > 0 ? (unrealized / position.cost_basis) * 100 : 0,
      day_change_pct: quote.change_pct === null ? position.day_change_pct : quote.change_pct * 100,
    };
  });
  if (delta === 0) return { ...detail, positions };
  const marketValue = detail.market_value + delta;
  const equity = detail.equity + delta;
  const totalPnl = detail.total_pnl + delta;
  const dayPnl = detail.day_pnl + delta;
  const weighted = positions.map((position) => ({
    ...position,
    weight_pct: equity > 0 ? (position.market_value / equity) * 100 : 0,
  }));
  // Returns are time-weighted: today's factor is (equity - today's transfers) / previous
  // close, so the live move changes only that last factor. A server older than 入金／出金
  // sends neither field and keeps the plain ratios.
  const flow = detail.day_net_transfer ?? 0;
  const dayBase = detail.equity - flow - detail.day_pnl;
  const exFlow = detail.equity - flow;
  const totalReturn =
    detail.contributed_capital != null
      ? exFlow > 0
        ? ((1 + detail.total_return_pct / 100) * ((exFlow + delta) / exFlow) - 1) * 100
        : detail.total_return_pct
      : detail.initial_cash > 0
        ? (totalPnl / detail.initial_cash) * 100
        : detail.total_return_pct;
  return {
    ...detail,
    positions: weighted,
    market_value: marketValue,
    equity,
    unrealized_pnl: detail.unrealized_pnl + delta,
    total_pnl: totalPnl,
    total_return_pct: totalReturn,
    day_pnl: dayPnl,
    day_return_pct: dayBase > 0 ? (dayPnl / dayBase) * 100 : detail.day_return_pct,
  };
}

/* ---- Equity chart ------------------------------------------------------------------------ */

export type EquitySeries = {
  dates: string[];
  equity: number[];
  /** Benchmark rebased so it starts at the account's equity on the first day both exist. */
  benchmark: (number | null)[];
  hasBenchmark: boolean;
  /** Net deposits - withdrawals counted on each day (0 when none or the API is older). */
  flows: number[];
};

/**
 * The account's equity against 加權指數, as if the account's transfers had been made in the
 * index: from the first day both exist the benchmark starts at the account's equity and then
 * follows `B_t = B_{t-1} x idx_t / idx_{t-1} + F_t` (a deposit buys the index that day, a
 * withdrawal sells it; floored at 0). A day without an index close carries its transfer to
 * the next day that has one. Without any transfer this is exactly the plain rebase
 * `equity_0 x idx_t / idx_0`.
 */
export function rebaseBenchmark(points: EquityPoint[]): EquitySeries {
  const baseIndex = points.findIndex((p) => p.benchmark_close != null && p.benchmark_close > 0 && p.equity > 0);
  const base = baseIndex >= 0 ? points[baseIndex] : null;
  const flows = points.map((p) => p.net_transfer ?? 0);
  const withFlows = flows.some((flow) => flow !== 0);
  let value = base ? base.equity : 0;
  let lastClose = base ? (base.benchmark_close as number) : 0;
  let pending = 0;
  const benchmark = points.map((point, index) => {
    if (!base || index < baseIndex) return null;
    if (index === baseIndex) return base.equity;
    const close = point.benchmark_close;
    if (!withFlows) {
      if (close == null || close <= 0) return null;
      return (close / (base.benchmark_close as number)) * base.equity;
    }
    if (close == null || close <= 0) {
      pending += flows[index];
      return null;
    }
    value = Math.max(0, value * (close / lastClose) + pending + flows[index]);
    lastClose = close;
    pending = 0;
    return value;
  });
  return {
    dates: points.map((p) => p.date),
    equity: points.map((p) => p.equity),
    benchmark,
    hasBenchmark: benchmark.filter((v) => v !== null).length >= 2,
    flows,
  };
}

export type EquityRange = "1m" | "3m" | "all";

/** `days` sent to GET .../equity for each range ("all" asks for a year). */
export const RANGE_DAYS: Record<EquityRange, number> = { "1m": 30, "3m": 90, all: 365 };

/* ---- Deep link & request bodies ---------------------------------------------------------- */

export type DeepLink = { symbol: string | null; side: OrderSide | null; lots: number | null };

/** `/paper?symbol=2330.TW&side=buy&lots=2`; anything unrecognised is ignored. */
export function parseDeepLink(params: { get(name: string): string | null } | null | undefined): DeepLink {
  const symbolRaw = params?.get("symbol");
  const sideRaw = params?.get("side")?.toLowerCase();
  const lotsRaw = params?.get("lots");
  return {
    symbol: symbolRaw ? normalizeSymbolInput(symbolRaw) : null,
    side: sideRaw === "buy" || sideRaw === "sell" ? sideRaw : null,
    lots: lotsRaw ? parseLots(lotsRaw) : null,
  };
}

export const paperLink = (symbol: string, side: OrderSide = "buy") =>
  `/paper?symbol=${encodeURIComponent(symbol)}&side=${side}`;

/** The order request for the ticket's current inputs, or null while they are incomplete. */
export function buildOrderBody(input: {
  symbol: string | null;
  side: OrderSide;
  orderType: OrderType;
  priceText: string;
  lotsText: string;
}): OrderRequest | null {
  const symbol = input.symbol ? normalizeSymbolInput(input.symbol) : null;
  const lots = parseLots(input.lotsText);
  if (!symbol || lots === null) return null;
  if (input.orderType === "market") return { symbol, side: input.side, order_type: "market", lots };
  const price = parsePrice(input.priceText);
  if (price === null) return null;
  return { symbol, side: input.side, order_type: "limit", limit_price: round2(price), lots };
}

/* ---- Formatting -------------------------------------------------------------------------- */

export const formatMoney = (value: number) =>
  Math.round(value).toLocaleString("zh-TW", { maximumFractionDigits: 0 });

/** "+3,200" / "−1,200" / "0". */
export const formatSignedMoney = (value: number) => formatSigned(Math.round(value), 0);

/** The API sends percentages already multiplied by 100: 1.38 -> "+1.38%". */
export const formatSignedPercent = (value: number, digits = 2) => `${formatSigned(value, digits)}%`;

export const formatPercent = (value: number, digits = 1) => `${value.toFixed(digits)}%`;

export function toneClass(value: number | null | undefined): string {
  if (value == null) return "text-muted";
  const direction = directionOf(value);
  return direction === "flat" ? "text-muted" : `text-${direction}`;
}

const clockFormat = new Intl.DateTimeFormat("zh-TW", {
  timeZone: "Asia/Taipei",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});
const dateTimeFormat = new Intl.DateTimeFormat("zh-TW", {
  timeZone: "Asia/Taipei",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function validDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isFinite(date.getTime()) ? date : null;
}

/** 13:24:55 in Taipei time. */
export function formatClock(iso: string | null | undefined): string {
  const date = validDate(iso);
  return date ? clockFormat.format(date) : "—";
}

/** 11/27 13:24 in Taipei time. */
export function formatDateTime(iso: string | null | undefined): string {
  const date = validDate(iso);
  return date ? dateTimeFormat.format(date).replace(/\s+/g, " ") : "—";
}

/** 2026-11-30 -> 11/30. */
export const formatSessionDate = (iso: string) => iso.slice(5).replace("-", "/");

export const STATUS_LABEL: Record<OrderStatus, string> = {
  working: "委託中",
  filled: "已成交",
  cancelled: "已取消",
  expired: "已過期",
  rejected: "已拒絕",
};

export const SOURCE_LABEL: Record<PriceSource, string> = {
  tick: "逐筆",
  snapshot: "快照",
  minute_bar: "分K",
  open: "開盤",
};

export const SIDE_LABEL: Record<OrderSide, string> = { buy: "買進", sell: "賣出" };

/** Discount as Taiwanese traders say it: 1 -> 不打折, 0.6 -> 6 折, 0.28 -> 2.8 折. */
export function discountLabel(discount: number): string {
  if (discount >= 1) return "不打折";
  return `${Number((discount * 10).toFixed(2))} 折`;
}

/** 500_000 -> "50 萬", 10_000_000 -> "1,000 萬". */
export const wanLabel = (value: number) => `${(value / 10_000).toLocaleString("zh-TW")} 萬`;
