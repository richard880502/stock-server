// Paper trading (模擬交易) API: types and endpoint functions for PAPER_SPEC.md section 5.
// Kept apart from endpoints.ts / types.ts on purpose. Money is integer NT$, prices are
// numbers with at most 2 decimals, times are ISO 8601 UTC, percentages are in percent
// (1.38 = 1.38%, NOT a ratio).
import { apiUrl, fetchJson, postJson } from "./client";

type Init = Pick<RequestInit, "signal">;

export type OrderSide = "buy" | "sell";
export type OrderType = "market" | "limit";
export type OrderStatus = "working" | "filled" | "cancelled" | "expired" | "rejected";
export type PriceSource = "tick" | "snapshot" | "minute_bar" | "open";

export type AccountSummary = {
  id: string;
  name: string;
  initial_cash: number;
  cash: number;
  available_cash: number;
  market_value: number;
  equity: number;
  unrealized_pnl: number;
  realized_pnl: number;
  /** Gross cash dividends credited since the last reset; absent on an API older than dividends. */
  dividend_income?: number;
  /**
   * Initial cash + deposits - withdrawals of the current epoch; total_pnl = equity - this.
   * Absent on an API older than 入金／出金.
   */
  contributed_capital?: number;
  /**
   * Net deposits - withdrawals of the latest session day's window (previous close, that day's
   * close]: they count as arriving at the start of the day.
   */
  day_net_transfer?: number;
  /**
   * Net deposits - withdrawals after that day's close (evening, weekend): already in `equity`,
   * in no return until the next session. day_pnl = equity - this - previous close -
   * day_net_transfer; day_return_pct = day_pnl / (previous close + day_net_transfer).
   */
  post_close_transfer?: number;
  total_pnl: number;
  /** Time-weighted since the last reset: deposits are not profit, withdrawals are not loss. */
  total_return_pct: number;
  day_pnl: number;
  day_return_pct: number;
  positions_count: number;
  working_orders: number;
  commission_discount: number;
  allow_day_trade: boolean;
  created_at: string;
  prices_as_of: string | null;
};

export type Position = {
  symbol: string;
  name: string;
  market: "TW" | "TWO";
  shares: number;
  lots: number;
  available_shares: number;
  avg_cost: number;
  cost_basis: number;
  price: number;
  price_time: string | null;
  price_delayed: boolean;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pct: number;
  day_change_pct: number | null;
  weight_pct: number;
  /** Cash dividends received on this holding (not part of the price-only unrealized figures). */
  dividend_income?: number;
};

export type AccountDetail = AccountSummary & {
  positions: Position[];
  benchmark: { name: string; return_pct: number; since: string } | null;
};

export type Fill = {
  id: string;
  order_id: string;
  symbol: string;
  side: OrderSide;
  shares: number;
  price: number;
  amount: number;
  commission: number;
  tax: number;
  day_trade: boolean;
  filled_at: string;
  price_source: PriceSource;
  price_time: string | null;
  realized_pnl: number | null;
  /** Not in the contract's Fill shape; present only if the API adds it. */
  name?: string;
};

export type Order = {
  id: string;
  symbol: string;
  name: string;
  side: OrderSide;
  order_type: OrderType;
  limit_price: number | null;
  lots: number;
  status: OrderStatus;
  source: "manual" | "ai";
  session_date: string;
  created_at: string;
  updated_at: string;
  reject_reason: string | null;
  reserved_cash: number;
  note: string | null;
  fill: Fill | null;
};

export type OrderPreview = {
  ok: boolean;
  reject_reason: string | null;
  symbol: string;
  name: string;
  side: OrderSide;
  order_type: OrderType;
  lots: number;
  shares: number;
  reference_price: number | null;
  last_price: number | null;
  last_price_time: string | null;
  price_delayed: boolean;
  limit_up: number | null;
  limit_down: number | null;
  tick: number | null;
  estimated_price: number | null;
  estimated_amount: number | null;
  estimated_commission: number | null;
  estimated_tax: number | null;
  estimated_total: number | null;
  reserve: number | null;
  available_cash_after: number | null;
  session_date: string;
  will_queue_for_next_session: boolean;
  warnings: string[];
};

export type EquityPoint = {
  date: string;
  equity: number;
  cash: number;
  market_value: number;
  benchmark_close: number | null;
  live: boolean;
  /** Net deposits - withdrawals counted on this day; absent on an older API. */
  net_transfer?: number;
  /** Live point only: transfers after this day's close, in `equity` but not in its move. */
  post_close_transfer?: number;
  /** Time-weighted return index, 100 at the first point; absent on an older API. */
  twr_index?: number | null;
};

export type OrderRequest = {
  symbol: string;
  side: OrderSide;
  order_type: OrderType;
  limit_price?: number;
  lots: number;
  note?: string;
  /** The AI proposal this order accepts (sent with the real submit only, never the preview). */
  proposal_id?: string;
};

export type NewAccount = {
  name: string;
  initial_cash: number;
  commission_discount: number;
  allow_day_trade: boolean;
};

export type AccountPatch = Partial<Pick<NewAccount, "name" | "commission_discount" | "allow_day_trade">>;

export type TickBand = { min: number; max: number | null; tick: number };

/**
 * GET /paper/rules. The contract says only "the effective cost/tick/limit/slippage rules as
 * data", so every field is optional and unknown extras are kept for display.
 */
export type PaperRules = {
  tick_bands?: TickBand[];
  board_lot?: number;
  price_limit_pct?: number;
  slippage_ticks?: number;
  commission_rate?: number;
  commission_min?: number;
  tax_rate?: number;
  etf_tax_rate?: number;
  /** Last day a 債券 ETF sale is exempt from the transaction tax (ISO date). */
  bond_etf_tax_exempt_through?: string;
  day_trade_tax_rate?: number;
  [extra: string]: unknown;
};

export type AccountList = { accounts: AccountSummary[]; max_accounts: number };

export type Page<T> = { items: T[]; hasMore: boolean | null };

const base = "/api/v1/paper";
const enc = encodeURIComponent;

/** Accept a bare array or an object wrapping it under one of `keys` (the list shapes are unspecified). */
export function unwrapList<T>(payload: unknown, keys: string[]): T[] {
  if (Array.isArray(payload)) return payload as T[];
  if (payload && typeof payload === "object") {
    for (const key of keys) {
      const value = (payload as Record<string, unknown>)[key];
      if (Array.isArray(value)) return value as T[];
    }
  }
  return [];
}

function pageOf<T>(payload: unknown, keys: string[]): Page<T> {
  const items = unwrapList<T>(payload, keys);
  const flag =
    payload && typeof payload === "object" && !Array.isArray(payload)
      ? (payload as Record<string, unknown>).has_more
      : undefined;
  return { items, hasMore: typeof flag === "boolean" ? flag : null };
}

export const listAccounts = (init?: Init) => fetchJson<AccountList>(apiUrl(`${base}/accounts`), init);

export const createAccount = (body: NewAccount) => postJson<AccountSummary>(apiUrl(`${base}/accounts`), body);

export const getAccount = (id: string, init?: Init) =>
  fetchJson<AccountDetail>(apiUrl(`${base}/accounts/${enc(id)}`), init);

export const updateAccount = (id: string, patch: AccountPatch) =>
  fetchJson<AccountSummary>(apiUrl(`${base}/accounts/${enc(id)}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });

/** `initialCash` starts the new epoch with that cash; leave it out to keep the current amount. */
export const resetAccount = (id: string, initialCash?: number) =>
  postJson<AccountSummary>(
    apiUrl(`${base}/accounts/${enc(id)}/reset`),
    initialCash == null ? {} : { initial_cash: initialCash },
  );

export const archiveAccount = (id: string) =>
  fetchJson<unknown>(apiUrl(`${base}/accounts/${enc(id)}`), { method: "DELETE" });

export const previewOrder = (id: string, body: OrderRequest, init?: Init) =>
  postJson<OrderPreview>(apiUrl(`${base}/accounts/${enc(id)}/orders?preview=true`), body, init);

export const placeOrder = (id: string, body: OrderRequest) =>
  postJson<Order>(apiUrl(`${base}/accounts/${enc(id)}/orders?preview=false`), body);

export async function listOrders(
  id: string,
  query: { status: "working" | "done" | "all"; limit: number; offset: number },
  init?: Init,
): Promise<Page<Order>> {
  const params = new URLSearchParams({
    status: query.status,
    limit: String(query.limit),
    offset: String(query.offset),
  });
  return pageOf<Order>(await fetchJson<unknown>(apiUrl(`${base}/accounts/${enc(id)}/orders?${params}`), init), [
    "orders",
    "items",
  ]);
}

export const cancelOrder = (id: string, orderId: string) =>
  fetchJson<unknown>(apiUrl(`${base}/accounts/${enc(id)}/orders/${enc(orderId)}`), { method: "DELETE" });

export async function listFills(
  id: string,
  query: { limit: number; offset: number },
  init?: Init,
): Promise<Page<Fill>> {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  return pageOf<Fill>(await fetchJson<unknown>(apiUrl(`${base}/accounts/${enc(id)}/fills?${params}`), init), [
    "fills",
    "items",
  ]);
}

export async function getEquity(id: string, days: number, init?: Init): Promise<EquityPoint[]> {
  const payload = await fetchJson<unknown>(apiUrl(`${base}/accounts/${enc(id)}/equity?days=${days}`), init);
  return unwrapList<EquityPoint>(payload, ["points", "equity", "snapshots", "items"]);
}

export const getRules = (init?: Init) => fetchJson<PaperRules>(apiUrl(`${base}/rules`), init);

/** One applied 除權息 on the account: cash credited on the ex-date and/or bonus shares added. */
export type CorporateAction = {
  id: string;
  symbol: string;
  name: string;
  ex_date: string;
  kind: "ex_dividend" | "ex_rights" | "ex_rights_dividend";
  entitled_shares: number;
  cash_amount: number;
  shares_added: number;
  cash_dividend_per_share: number | null;
  stock_dividend_per_share: number | null;
  applied_at: string;
};

export async function listCorporateActions(
  id: string,
  query: { limit: number; offset: number },
  init?: Init,
): Promise<Page<CorporateAction>> {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  return pageOf<CorporateAction>(
    await fetchJson<unknown>(apiUrl(`${base}/accounts/${enc(id)}/corporate-actions?${params}`), init),
    ["actions", "items"],
  );
}

export type TransferDirection = "in" | "out";

/** One deposit or withdrawal (入金／出金); `amount` is always positive. */
export type Transfer = {
  id: string;
  direction: TransferDirection;
  amount: number;
  at: string;
  note: string | null;
};

export type TransferBody = {
  direction: TransferDirection;
  amount: number;
  note?: string;
  /** Generated once per dialog open: a double click or retry cannot book twice. */
  request_id: string;
};

/** Deposit or withdraw simulated cash; answers the updated account summary. */
export const postTransfer = (id: string, body: TransferBody) =>
  postJson<AccountSummary>(apiUrl(`${base}/accounts/${enc(id)}/transfers`), body);

export async function listTransfers(
  id: string,
  query: { limit: number; offset: number },
  init?: Init,
): Promise<Page<Transfer>> {
  const params = new URLSearchParams({ limit: String(query.limit), offset: String(query.offset) });
  return pageOf<Transfer>(
    await fetchJson<unknown>(apiUrl(`${base}/accounts/${enc(id)}/transfers?${params}`), init),
    ["transfers", "items"],
  );
}
