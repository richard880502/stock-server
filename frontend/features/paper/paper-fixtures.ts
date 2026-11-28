// Realistic contract-shaped fixtures (PAPER_SPEC.md section 5) for tests and local review.
import type {
  AccountDetail,
  AccountSummary,
  CorporateAction,
  EquityPoint,
  Fill,
  Order,
  OrderPreview,
  PaperRules,
  Position,
  Transfer,
} from "@/lib/api/paper";

export const RULES: PaperRules = {
  tick_bands: [
    { min: 0, max: 10, tick: 0.01 },
    { min: 10, max: 50, tick: 0.05 },
    { min: 50, max: 100, tick: 0.1 },
    { min: 100, max: 500, tick: 0.5 },
    { min: 500, max: 1000, tick: 1 },
    { min: 1000, max: null, tick: 5 },
  ],
  board_lot: 1000,
  price_limit_pct: 10,
  slippage_ticks: 1,
  commission_rate: 0.001425,
  commission_min: 20,
  tax_rate: 0.003,
  etf_tax_rate: 0.001,
  day_trade_tax_rate: 0.0015,
};

export const ACCOUNT_MAIN: AccountSummary = {
  id: "acc-main",
  name: "主帳戶",
  initial_cash: 1_000_000,
  cash: 812_345,
  available_cash: 700_000,
  market_value: 201_500,
  equity: 1_013_845,
  unrealized_pnl: 3_200,
  realized_pnl: 10_645,
  total_pnl: 13_845,
  total_return_pct: 1.38,
  day_pnl: -1_200,
  day_return_pct: -0.12,
  positions_count: 2,
  working_orders: 1,
  commission_discount: 1,
  allow_day_trade: false,
  created_at: "2026-11-01T02:00:00Z",
  prices_as_of: "2026-11-27T05:24:55Z",
};

export const ACCOUNT_SECOND: AccountSummary = {
  ...ACCOUNT_MAIN,
  id: "acc-day",
  name: "當沖練習",
  initial_cash: 3_000_000,
  cash: 3_000_000,
  available_cash: 3_000_000,
  market_value: 0,
  equity: 3_000_000,
  unrealized_pnl: 0,
  realized_pnl: 0,
  total_pnl: 0,
  total_return_pct: 0,
  day_pnl: 0,
  day_return_pct: 0,
  positions_count: 0,
  working_orders: 0,
  commission_discount: 0.28,
  allow_day_trade: true,
};

/** A winner, and a loser whose price is delayed (symbol outside the live-quote cap). */
export const POSITION_WIN: Position = {
  symbol: "2330.TW",
  name: "台積電",
  market: "TW",
  shares: 2000,
  lots: 2,
  available_shares: 1000,
  avg_cost: 1075.25,
  cost_basis: 2_150_500,
  price: 1090,
  price_time: "2026-11-27T05:24:55Z",
  price_delayed: false,
  market_value: 2_180_000,
  unrealized_pnl: 29_500,
  unrealized_pct: 1.37,
  day_change_pct: 1.63,
  weight_pct: 68.4,
};

export const POSITION_LOSS_DELAYED: Position = {
  symbol: "6488.TWO",
  name: "環球晶",
  market: "TWO",
  shares: 1000,
  lots: 1,
  available_shares: 1000,
  avg_cost: 412.5,
  cost_basis: 412_500,
  price: 398,
  price_time: "2026-11-27T05:10:00Z",
  price_delayed: true,
  market_value: 398_000,
  unrealized_pnl: -14_500,
  unrealized_pct: -3.52,
  day_change_pct: -0.75,
  weight_pct: 12.5,
};

export const DETAIL_MAIN: AccountDetail = {
  ...ACCOUNT_MAIN,
  positions: [POSITION_WIN, POSITION_LOSS_DELAYED],
  benchmark: { name: "加權指數", return_pct: 0.9, since: "2026-11-01" },
};

export const DETAIL_EMPTY: AccountDetail = { ...ACCOUNT_SECOND, positions: [], benchmark: null };

export const FILL_BUY: Fill = {
  id: "fill-1",
  order_id: "ord-2",
  symbol: "2330.TW",
  side: "buy",
  shares: 1000,
  price: 1075,
  amount: 1_075_000,
  commission: 1_532,
  tax: 0,
  day_trade: false,
  filled_at: "2026-11-26T01:12:30Z",
  price_source: "tick",
  price_time: "2026-11-26T01:12:29Z",
  realized_pnl: null,
};

export const FILL_SELL: Fill = {
  id: "fill-2",
  order_id: "ord-3",
  symbol: "6488.TWO",
  side: "sell",
  shares: 1000,
  price: 420,
  amount: 420_000,
  commission: 599,
  tax: 1_260,
  day_trade: false,
  filled_at: "2026-11-25T05:40:00Z",
  price_source: "minute_bar",
  price_time: "2026-11-25T05:39:00Z",
  realized_pnl: 6_141,
};

export const ORDER_WORKING: Order = {
  id: "ord-1",
  symbol: "2330.TW",
  name: "台積電",
  side: "buy",
  order_type: "limit",
  limit_price: 1085,
  lots: 1,
  status: "working",
  source: "manual",
  session_date: "2026-11-27",
  created_at: "2026-11-27T01:00:00Z",
  updated_at: "2026-11-27T01:00:00Z",
  reject_reason: null,
  reserved_cash: 1_086_548,
  note: null,
  fill: null,
};

export const ORDER_FILLED: Order = {
  ...ORDER_WORKING,
  id: "ord-2",
  limit_price: null,
  order_type: "market",
  status: "filled",
  session_date: "2026-11-26",
  fill: FILL_BUY,
};

export const ORDER_REJECTED: Order = {
  ...ORDER_WORKING,
  id: "ord-4",
  symbol: "2317.TW",
  name: "鴻海",
  lots: 50,
  status: "rejected",
  reject_reason: "可用資金不足：需要 NT$ 5,010,000，目前可用 NT$ 700,000",
  reserved_cash: 0,
};

export const ORDER_CANCELLED: Order = { ...ORDER_WORKING, id: "ord-5", status: "cancelled", reserved_cash: 0 };

export const ORDERS_DONE: Order[] = [ORDER_FILLED, ORDER_REJECTED, ORDER_CANCELLED];

export const EQUITY_POINTS: EquityPoint[] = [
  { date: "2026-11-23", equity: 1_000_000, cash: 1_000_000, market_value: 0, benchmark_close: 27_000, live: false },
  { date: "2026-11-24", equity: 1_004_000, cash: 400_000, market_value: 604_000, benchmark_close: 27_054, live: false },
  { date: "2026-11-25", equity: 1_011_000, cash: 400_000, market_value: 611_000, benchmark_close: 27_081, live: false },
  { date: "2026-11-26", equity: 1_015_045, cash: 812_345, market_value: 202_700, benchmark_close: 27_108, live: false },
  { date: "2026-11-27", equity: 1_013_845, cash: 812_345, market_value: 201_500, benchmark_close: 27_123.4, live: true },
];

const PREVIEW_BASE: OrderPreview = {
  ok: true,
  reject_reason: null,
  symbol: "2330.TW",
  name: "台積電",
  side: "buy",
  order_type: "limit",
  lots: 1,
  shares: 1000,
  reference_price: 1072,
  last_price: 1090,
  last_price_time: "2026-11-27T05:24:55Z",
  price_delayed: false,
  limit_up: 1175,
  limit_down: 965,
  tick: 5,
  estimated_price: 1090,
  estimated_amount: 1_090_000,
  estimated_commission: 1_553,
  estimated_tax: 0,
  estimated_total: 1_091_553,
  reserve: 1_091_553,
  available_cash_after: 8_447,
  session_date: "2026-11-27",
  will_queue_for_next_session: false,
  warnings: ["此筆張數超過該股 20 日均量的 1%，實際市場可能無法全數成交"],
};

export const PREVIEW_OK = PREVIEW_BASE;

export const PREVIEW_REJECTED: OrderPreview = {
  ...PREVIEW_BASE,
  ok: false,
  reject_reason: "可用資金不足：需要 NT$ 5,010,000，目前可用 NT$ 700,000",
  lots: 5,
  shares: 5000,
  warnings: [],
  available_cash_after: null,
};

export const PREVIEW_NEXT_SESSION: OrderPreview = {
  ...PREVIEW_BASE,
  warnings: [],
  session_date: "2026-11-30",
  will_queue_for_next_session: true,
};

export const PREVIEW_SELL: OrderPreview = {
  ...PREVIEW_BASE,
  side: "sell",
  order_type: "market",
  estimated_price: 1085,
  estimated_amount: 1_085_000,
  estimated_commission: 1_546,
  estimated_tax: 3_255,
  estimated_total: 1_080_199,
  reserve: null,
  available_cash_after: 1_780_199,
  warnings: [],
};

/** A cash + stock dividend, and a cash-only one, as the corporate-actions route returns them. */
export const ACTION_BOTH: CorporateAction = {
  id: "act-1",
  symbol: "2330.TW",
  name: "台積電",
  ex_date: "2026-11-27",
  kind: "ex_rights_dividend",
  entitled_shares: 2000,
  cash_amount: 5_000,
  shares_added: 100,
  cash_dividend_per_share: 2.5,
  stock_dividend_per_share: 0.05,
  applied_at: "2026-11-26T23:55:00Z",
};

export const ACTION_CASH: CorporateAction = {
  ...ACTION_BOTH,
  id: "act-2",
  symbol: "6488.TWO",
  name: "環球晶",
  ex_date: "2026-11-20",
  kind: "ex_dividend",
  entitled_shares: 1000,
  cash_amount: 3_000,
  shares_added: 0,
  cash_dividend_per_share: 3,
  stock_dividend_per_share: null,
};

/** The main account after those dividends were credited. */
export const DETAIL_WITH_DIVIDENDS: AccountDetail = { ...DETAIL_MAIN, dividend_income: 8_000 };

/** Today's deposit of 1,000,000 on top of a flat 1,000,000 account, then the market's +0.5%. */
export const DETAIL_AFTER_DEPOSIT: AccountDetail = {
  ...DETAIL_MAIN,
  initial_cash: 1_000_000,
  contributed_capital: 2_000_000,
  day_net_transfer: 1_000_000,
  equity: 2_005_000,
  total_pnl: 5_000,
  total_return_pct: 0.5,
  day_pnl: 5_000,
  day_return_pct: 0.5,
};

export const TRANSFERS: Transfer[] = [
  { id: "xfer-2", direction: "out", amount: 200_000, at: "2026-11-27T03:10:00Z", note: null },
  { id: "xfer-1", direction: "in", amount: 1_000_000, at: "2026-11-26T02:00:00Z", note: "年終獎金" },
];

/** A deposit of 1,000,000 on 11/25 (flow on that day only). */
export const EQUITY_POINTS_WITH_DEPOSIT: EquityPoint[] = EQUITY_POINTS.map((point, i) =>
  i === 2
    ? { ...point, equity: point.equity + 1_000_000, cash: point.cash + 1_000_000, net_transfer: 1_000_000 }
    : i > 2
      ? { ...point, equity: point.equity + 1_000_000, cash: point.cash + 1_000_000, net_transfer: 0 }
      : { ...point, net_transfer: 0 },
);
