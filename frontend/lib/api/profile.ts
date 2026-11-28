/**
 * Types and endpoint functions for the official-data feeds behind 「個股全貌」 and the market
 * 「期貨與選擇權」 section. They mirror the backend pydantic models (see
 * src/quant_signal/domain/{fundamentals,short_data,derivatives}.py and domain/models.py);
 * every nullable backend field is typed nullable here.
 *
 * Units: statement / revenue amounts are NT$ thousands (千元); `*_pct` fields are percent
 * (5.0 = 5%); percentiles are 0-100; lots (張) = 1,000 shares; futures open interest and
 * volume are standard-contract equivalents (口).
 */
import { apiUrl, fetchJson } from "./client";
import type { ScreenerEntry } from "./types";

type Init = RequestInit;

/* ---- GET /stocks/{symbol}/fundamentals -------------------------------------------- */

export type ValuationPoint = {
  date: string;
  pe_ratio: number | null;
  pb_ratio: number | null;
  dividend_yield_pct: number | null;
};

export type ValuationSection = {
  date: string;
  pe_ratio: number | null;
  pb_ratio: number | null;
  dividend_yield_pct: number | null;
  pe_percentile_250d: number | null;
  pb_percentile_250d: number | null;
  yield_percentile_250d: number | null;
  /** Weekly-sampled history, oldest first. */
  history: ValuationPoint[];
};

export type MonthRevenuePoint = {
  /** "YYYY-MM" */
  period: string;
  /** NT$ thousands. */
  revenue: number;
  yoy_pct: number | null;
  mom_pct: number | null;
};

export type RevenueSection = {
  months: MonthRevenuePoint[];
  yoy_3m_pct: number | null;
  yoy_12m_pct: number | null;
  is_12m_high: boolean | null;
  growth_streak_months: number;
};

export type QuarterPoint = {
  /** "2026Q3" */
  period: string;
  /** NT$ thousands, single quarter. */
  revenue: number | null;
  gross_margin_pct: number | null;
  operating_margin_pct: number | null;
  net_margin_pct: number | null;
  eps: number | null;
};

export type EarningsSection = {
  quarters: QuarterPoint[];
  eps_ttm: number | null;
  eps_yoy_pct: number | null;
  roe_ttm_pct: number | null;
};

export type StockFundamentals = {
  symbol: string;
  as_of: string;
  valuation: ValuationSection | null;
  revenue: RevenueSection | null;
  earnings: EarningsSection | null;
  notes: string[];
};

/* ---- GET /stocks/{symbol}/holders -------------------------------------------------- */

export type HolderPoint = {
  data_date: string;
  pct_ge400: number;
  pct_ge1000: number;
  holders_ge400: number;
  holders_ge1000: number;
  retail_holders: number;
  retail_pct: number;
  total_holders: number;
  total_shares: number;
  available_at: string;
};

export type StockHolders = {
  symbol: string;
  as_of: string;
  series: HolderPoint[];
  shares_outstanding: number | null;
  foreign_shares: number | null;
  foreign_holding_pct: number | null;
  foreign_data_date: string | null;
  note: string;
};

/* ---- GET /stocks/{symbol}/short-interest ------------------------------------------- */

export type ShortDay = {
  date: string;
  sbl_balance_lots: number | null;
  sbl_sold_lots: number | null;
  sbl_returned_lots: number | null;
  margin_short_balance_lots: number | null;
  day_trade_ratio_pct: number | null;
};

export type UpcomingEventKind =
  | "ex_dividend"
  | "ex_rights"
  | "ex_rights_dividend"
  | "investor_conference";

export type UpcomingEvent = {
  date: string;
  kind: UpcomingEventKind;
  label: string;
  /** NT$ per share. */
  cash_dividend: number | null;
  /** New shares per 1 share held (0.05 = 50 per 1,000). */
  stock_dividend: number | null;
};

export type InsiderPledge = {
  symbol: string;
  /** "YYYY-MM" */
  period: string;
  pledge_ratio_pct: number | null;
  holding_shares: number;
  pledged_shares: number;
};

export type StockShortInterest = {
  symbol: string;
  as_of: string;
  days: ShortDay[];
  sbl_change_5d_pct: number | null;
  sbl_change_20d_pct: number | null;
  /** 借券餘額 / 發行股數, percent. */
  sbl_pct_of_shares: number | null;
  days_to_cover: number | null;
  day_trade_ratio_5d_pct: number | null;
  day_trade_ratio_20d_pct: number | null;
  insiders: InsiderPledge | null;
  upcoming_events: UpcomingEvent[];
  notes: string[];
};

/* ---- GET /stocks/{symbol}/futures --------------------------------------------------- */

export type StockFuturesPoint = {
  date: string;
  open_interest: number | null;
  volume: number;
  near_month: string | null;
  near_settlement: number | null;
  basis_pct: number | null;
};

export type StockFutures = {
  symbol: string;
  has_futures: boolean;
  days: StockFuturesPoint[];
  oi_change_1d: number | null;
  oi_change_5d: number | null;
  notes: string[];
};

/* ---- GET /stocks/{symbol}/factors ---------------------------------------------------- */

export type StockFactors = {
  version: string;
  symbol: string;
  as_of: string | null;
  data_through: string | null;
  flow_through: string | null;
  universe: number;
  notice: string | null;
  /** null when the stock is not in the screener snapshot. */
  entry: ScreenerEntry | null;
};

/* ---- GET /market/derivatives --------------------------------------------------------- */

export type MarketDerivativesDay = {
  date: string;
  foreign_tx_net_oi: number | null;
  /** 大台等值, contracts. */
  foreign_tx_equiv_net_oi: number | null;
  foreign_net_oi_change: number | null;
  trust_tx_net_oi: number | null;
  dealer_tx_net_oi: number | null;
  /** Put/Call ratios in percent (82.07 = 82.07%). */
  pc_volume_ratio: number | null;
  pc_oi_ratio: number | null;
  foreign_call_net_oi: number | null;
  foreign_put_net_oi: number | null;
  top5_net_oi: number | null;
  top10_net_oi: number | null;
  vix: number | null;
};

export type MarketDerivativesLatest = MarketDerivativesDay & {
  max_call_oi_strike: number | null;
  max_put_oi_strike: number | null;
  max_call_oi_expiry: string | null;
  max_put_oi_expiry: string | null;
  weekly_max_call_oi_strike: number | null;
  weekly_max_put_oi_strike: number | null;
  weekly_expiry: string | null;
};

export type MarketDerivatives = {
  as_of: string;
  data_through: string | null;
  days: MarketDerivativesDay[];
  latest: MarketDerivativesLatest | null;
  notes: string[];
};

/* ---- Client ---------------------------------------------------------------------------- */

const enc = encodeURIComponent;
const withQuery = (path: string, params: Record<string, string | number | null | undefined>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return apiUrl(text ? `${path}?${text}` : path);
};

export const getStockFundamentals = (symbol: string, asOf?: string | null, init?: Init) =>
  fetchJson<StockFundamentals>(
    withQuery(`/api/v1/stocks/${enc(symbol)}/fundamentals`, { as_of: asOf }),
    init,
  );

export const getStockHolders = (symbol: string, asOf?: string | null, weeks = 52, init?: Init) =>
  fetchJson<StockHolders>(
    withQuery(`/api/v1/stocks/${enc(symbol)}/holders`, { as_of: asOf, weeks }),
    init,
  );

export const getStockShortInterest = (symbol: string, asOf?: string | null, days = 60, init?: Init) =>
  fetchJson<StockShortInterest>(
    withQuery(`/api/v1/stocks/${enc(symbol)}/short-interest`, { as_of: asOf, days }),
    init,
  );

export const getStockFutures = (symbol: string, asOf?: string | null, days = 60, init?: Init) =>
  fetchJson<StockFutures>(
    withQuery(`/api/v1/stocks/${enc(symbol)}/futures`, { as_of: asOf, days }),
    init,
  );

export const getStockFactors = (symbol: string, asOf?: string | null, init?: Init) =>
  fetchJson<StockFactors>(
    withQuery(`/api/v1/stocks/${enc(symbol)}/factors`, { as_of: asOf }),
    init,
  );

export const getMarketDerivatives = (asOf?: string | null, days = 60, init?: Init) =>
  fetchJson<MarketDerivatives>(withQuery("/api/v1/market/derivatives", { as_of: asOf, days }), init);
