// Response and request shapes returned by the Quant Signal REST API.

export type Regime = "risk_on" | "neutral" | "risk_off";
export type SignalAction = "increase" | "hold" | "reduce" | "avoid";

export type Alert = {
  code: string;
  severity: "info" | "medium" | "high";
  title: string;
  evidence: Record<string, number | string>;
};

export type Signal = {
  symbol: string;
  as_of: string;
  strategy_version: string;
  score: number;
  action: SignalAction;
  regime: Regime;
  confidence: number;
  factors: {
    trend: number;
    momentum: number;
    volume: number;
    relative_strength: number;
    risk_quality: number;
  };
  alerts: Alert[];
  data_available_at: string;
  observations: number;
};

export type MarketEnvironment = {
  market: string;
  as_of: string;
  strategy_version: string;
  score: number;
  regime: Regime;
  confidence: number;
  factors: {
    participation: number;
    advance_decline: number;
    breadth_momentum: number;
    volume_breadth: number;
    institutional_flow: number;
    leverage_quality: number;
  };
  alerts: Alert[];
  latest: {
    total_issues: number;
    advancing_issues: number;
    declining_issues: number;
    above_ma20_issues: number;
    foreign_net_flow: number;
    investment_trust_net_flow: number;
    dealer_net_flow: number;
    futures_net_open_interest: number;
    margin_balance: number;
    turnover_value: number;
    source: string;
  };
  data_available_at: string;
  observations: number;
};

export type BranchTrader = {
  branch_code: string;
  branch_name: string;
  net_shares: number;
  estimated_cost: number | null;
  win_rate: number | null;
};

export type ChipFlow = {
  symbol: string;
  as_of: string;
  score: number;
  confidence: number;
  institutional_observations: number;
  branch_observations: number;
  branch_data_available: boolean;
  branch_data_status: string;
  metrics: {
    foreign_net_5d: number;
    investment_trust_net_5d: number;
    dealer_net_5d: number;
    total_net_20d: number;
    flow_volume_ratio_5d: number;
    foreign_streak: number;
    investment_trust_streak: number;
    foreign_holding_ratio: number | null;
    foreign_holding_change_5d: number | null;
    branch_buy_concentration_5d: number | null;
    branch_sell_concentration_5d: number | null;
    branch_estimated_cost: number | null;
    branch_day_trade_ratio: number | null;
  };
  top_buyers: BranchTrader[];
  top_sellers: BranchTrader[];
  alerts: Alert[];
};

export type LlmStatus = {
  enabled: boolean;
  configured: boolean;
  missing: string[];
  model: string | null;
  base_url: string;
};

export type DataStatus = {
  real_data_ready: boolean;
  bar_series: {
    key: string;
    source: string;
    first_date: string;
    latest_date: string;
    observations: number;
    adjusted_observations: number;
    is_real: boolean;
  }[];
  market_series: {
    key: string;
    source: string;
    first_date: string;
    latest_date: string;
    observations: number;
    is_real: boolean;
  }[];
};

export type Judgement = {
  headline: string;
  stance: "bullish" | "neutral" | "cautious" | "bearish";
  operation_guide: string;
  key_evidence: string[];
  risk_warnings: string[];
  scenarios: { name: string; condition: string; response: string }[];
  limitations: string[];
};

export type DebateCase = {
  stance: "bullish" | "bearish";
  thesis: string;
  key_evidence: string[];
  counterpoints_to_address: string[];
};

export type DebateReport = {
  model: string;
  judge_model: string;
  prompt_version: string;
  bull_case: DebateCase;
  bear_case: DebateCase;
  judgement: Judgement;
};

/** Payload of the `meta` / `delta` / `error` events on the LLM SSE stream. */
export type LlmStreamPayload = {
  text?: string;
  model?: string;
  message?: string;
  status?: number;
};

export type EquityPoint = { trading_date: string; equity: number; position: number };

export type BacktestJob = {
  id: string;
  status: "queued" | "running" | "completed" | "failed";
  error: string | null;
  result: null | {
    observations: number;
    trades: number;
    total_return: number;
    annualized_return: number;
    annualized_volatility: number;
    sharpe: number | null;
    max_drawdown: number;
    equity_curve: EquityPoint[];
    buy_hold_curve?: EquityPoint[];
    buy_hold_return?: number | null;
    excess_return?: number | null;
    warnings?: string[];
    transactions?: {
      trading_date: string;
      side: string;
      price: number;
      cost: number;
      equity: number;
    }[];
  };
};

export type ForecastPoint = {
  trading_date: string;
  median: number;
  lower: number;
  upper: number;
};

export type ForecastReport = {
  symbol: string;
  model: string;
  data_through: string;
  last_close: number;
  expected_return: number;
  lookback: number;
  horizon: number;
  history: { trading_date: string; close: number }[];
  points: ForecastPoint[];
  evaluation: {
    windows: number;
    mae_pct: number | null;
    naive_mae_pct: number | null;
    direction_accuracy: number | null;
    interval_coverage: number | null;
  };
  warnings: string[];
};

export type ForecastStatus = { kronos: boolean };

/** One row of GET /api/v1/instruments/search. */
export type Instrument = { symbol: string; name: string; market: "TW" | "TWO" };

export type LoginResponse = { api_key: string };

/** Body of the HTTP 503 the analysis endpoints return while history is being fetched. */
export type SyncPendingBody = {
  detail: string;
  sync_pending: true;
  symbol: string;
};

/** Body shared by the LLM / debate / backtest request payloads. */
export type AnalysisRequest = {
  symbol: string;
  as_of: string;
  market: "TW" | "TWO";
  benchmark: string | null;
  strategy_version: string;
  backtest_job_id: string | null;
};

export type BacktestRequest = {
  symbol: string;
  benchmark: string | null;
  market: "TW" | "TWO" | null;
  strategy_version: string;
  start: string;
  end: string;
  entry_score: number;
  exit_score: number;
  fee_bps: number;
  slippage_bps: number;
  sell_tax_bps: number;
  initial_capital: number;
  use_chip_filter: boolean;
  chip_entry_score: number;
  chip_exit_score: number;
};

export type ForecastRequest = {
  symbol: string;
  as_of: string;
  horizon: number;
  lookback: number;
  model: string;
};

export type SyncSymbolRequest = { symbol: string; start: string; end: string };

// ---- Market rankings and capital flows (GET /api/v1/market/*) ----------------

export type MarketMetric =
  | "foreign_net"
  | "trust_net"
  | "dealer_net"
  | "total_net"
  | "volume"
  | "turnover"
  | "change_pct"
  | "margin_change"
  | "short_change";

/** ALL = 上市 + 上櫃, TW = 上市, TWO = 上櫃. */
export type MarketScope = "ALL" | "TW" | "TWO";

export type RankingEntry = {
  rank: number;
  symbol: string;
  name: string;
  market: "TW" | "TWO";
  close: number;
  /** Percent, e.g. 2.5 means +2.5%. */
  day_change_pct: number;
  /** The metric over the window (flows and credit summed, price change compounded). */
  value: number;
  volume_lots: number;
  turnover_value: number;
};

export type MarketRankings = {
  metric: MarketMetric;
  market: MarketScope;
  window: number;
  order: "desc" | "asc";
  as_of: string | null;
  /** Newest session that has data for this metric. */
  data_through: string | null;
  window_start: string | null;
  trading_days: number;
  /** 張 for share-based metrics and credit changes, 元 for turnover, % for change_pct. */
  unit: "張" | "元" | "%";
  /** A newer session whose quotes are out but whose report is not published yet. */
  awaiting_report_date: string | null;
  backfill_pending: boolean;
  entries: RankingEntry[];
};

export type MarketFlowDay = {
  trading_date: string;
  /**
   * NT$ estimates (net shares x that day's close). A day that is not reported
   * yet arrives as 0, so always check institutional_reported / credit_reported.
   */
  foreign_net_value: number;
  trust_net_value: number;
  dealer_net_value: number;
  foreign_net_shares: number;
  trust_net_shares: number;
  dealer_net_shares: number;
  turnover_value: number;
  /** 張. */
  margin_balance: number;
  short_balance: number;
  advancing: number;
  declining: number;
  unchanged: number;
  institutional_reported: boolean;
  credit_reported: boolean;
};

export type MarketFlows = {
  market: MarketScope;
  as_of: string | null;
  data_through: string | null;
  /** Server-side description of how the NT$ values are estimated. */
  value_basis: string;
  backfill_pending: boolean;
  days: MarketFlowDay[];
};

export type MarketDataStatus = {
  market: "TW" | "TWO";
  first_date: string | null;
  last_date: string | null;
  trading_days: number;
  stocks_on_last_day: number;
  /** 0..1 share of sessions with institutional / credit reports. */
  institutional_coverage: number;
  credit_coverage: number;
};
