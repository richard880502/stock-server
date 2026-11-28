from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any, Literal, NamedTuple
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SignalAction(StrEnum):
    INCREASE = "increase"
    HOLD = "hold"
    REDUCE = "reduce"
    AVOID = "avoid"


class MarketRegime(StrEnum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"


class AlertSeverity(StrEnum):
    INFO = "info"
    MEDIUM = "medium"
    HIGH = "high"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MarketStockDay(BaseModel):
    """One stock's official end-of-day quote, institutional flow and credit balance.

    Flow and credit fields are ``None`` when the exchange had not published that
    report for the day. Share counts are in shares; credit balances in lots.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    name: str
    market: Literal["TW", "TWO"]
    trading_date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0)
    turnover_value: float = Field(ge=0)
    change: float = 0.0
    foreign_net: int | None = None
    trust_net: int | None = None
    dealer_net: int | None = None
    total_net: int | None = None
    margin_balance: int | None = None
    margin_change: int | None = None
    short_balance: int | None = None
    short_change: int | None = None
    available_at: datetime
    source: str


class MarketIndustry(BaseModel):
    """One listed/OTC stock's official industry (TWSE/TPEx 產業別)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    market: Literal["TW", "TWO"]
    industry_code: str
    industry_name: str
    source: str
    available_at: datetime


class IntradaySnapshot(BaseModel):
    """One stock's intraday state at a 10-minute collector tick.

    ``change_pct`` is a ratio versus the previous close (0.0123 = +1.23%);
    ``volume_lots`` and ``amount_twd`` are cumulative for the session so far.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    taken_at: datetime
    price: float = Field(gt=0)
    change_pct: float
    volume_lots: int = Field(ge=0)
    amount_twd: float = Field(ge=0)
    available_at: datetime

    @model_validator(mode="after")
    def _timezone_aware(self) -> IntradaySnapshot:
        if self.taken_at.tzinfo is None or self.available_at.tzinfo is None:
            raise ValueError("taken_at and available_at must be timezone-aware")
        return self


RankingMetric = Literal[
    "foreign_net",
    "trust_net",
    "dealer_net",
    "total_net",
    "volume",
    "turnover",
    "change_pct",
    "margin_change",
    "short_change",
]
RANKING_METRICS: tuple[str, ...] = (
    "foreign_net",
    "trust_net",
    "dealer_net",
    "total_net",
    "volume",
    "turnover",
    "change_pct",
    "margin_change",
    "short_change",
)


class RankingEntry(BaseModel):
    rank: int
    symbol: str
    name: str
    market: Literal["TW", "TWO"]
    close: float
    day_change_pct: float | None = None
    value: float
    volume_lots: int
    turnover_value: float


class MarketDataVersion(NamedTuple):
    """Fingerprint of the whole-market tables: equal versions mean the same rows.

    ``newest_date`` is the latest stored session (None when empty); ``fingerprint`` is
    opaque but hashable and changes whenever a sync adds or updates the newest rows.
    """

    newest_date: date | None
    fingerprint: tuple[Any, ...]


class MarketRankingRow(BaseModel):
    """One stock's ranking value (final units) with its newest session's fields."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    name: str
    market: Literal["TW", "TWO"]
    close: float
    change: float
    volume: int
    turnover_value: float
    value: float


class MarketRanking(BaseModel):
    metric: RankingMetric
    market: Literal["ALL", "TW", "TWO"]
    window: int
    order: Literal["desc", "asc"]
    as_of: date | None
    data_through: date | None
    window_start: date | None
    trading_days: int
    unit: str
    # Newest session with quotes whose reports for this metric are not out yet.
    awaiting_report_date: date | None = None
    backfill_pending: bool = False
    entries: list[RankingEntry]


class MarketFlowDay(BaseModel):
    trading_date: date
    foreign_net_value: float
    trust_net_value: float
    dealer_net_value: float
    foreign_net_shares: int
    trust_net_shares: int
    dealer_net_shares: int
    turnover_value: float
    margin_balance: int
    short_balance: int
    advancing: int
    declining: int
    unchanged: int
    institutional_reported: bool = True
    credit_reported: bool = True


class MarketFlowSeries(BaseModel):
    market: Literal["ALL", "TW", "TWO"]
    as_of: date | None
    data_through: date | None
    value_basis: str = "net shares x that day's close (estimate, not the official NT$ amount)"
    backfill_pending: bool = False
    days: list[MarketFlowDay]


class MarketDataStatus(BaseModel):
    market: Literal["TW", "TWO"]
    first_date: date | None
    last_date: date | None
    trading_days: int
    stocks_on_last_day: int
    institutional_coverage: float
    credit_coverage: float


class FetchCoverage(BaseModel):
    """Records that one slice of an upstream dataset has already been fetched.

    ``period`` is a month (``2026-07``) or a day (``2026-07-31``). An ``empty``
    status means the upstream answered but had nothing, e.g. a holiday or a
    month before the symbol was listed, so it must not be requested again.
    """

    source: str
    dataset: str
    symbol: str
    period: str
    status: Literal["ok", "empty"]
    row_count: int = Field(default=0, ge=0)
    fetched_at: datetime


class SyncJobKind(StrEnum):
    SYMBOL = "symbol"
    INSTITUTIONS = "institutions"
    MARKET = "market"


class SyncJob(BaseModel):
    id: UUID
    kind: SyncJobKind
    symbol: str
    status: JobStatus
    lookback_days: int = Field(default=500, ge=1)
    attempts: int = Field(default=0, ge=0)
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class DataSeriesStatus(BaseModel):
    key: str
    source: str
    first_date: date
    latest_date: date
    observations: int = Field(ge=0)
    adjusted_observations: int = Field(default=0, ge=0)
    is_real: bool


class Instrument(BaseModel):
    symbol: str
    name: str | None = None
    market: str | None = None
    # True when full adjusted history is stored (signals, backtests and forecasts work
    # immediately); False for stocks that only exist in the whole-market snapshot.
    tracked: bool | None = None


class DataStatus(BaseModel):
    generated_at: datetime
    real_data_ready: bool
    bar_series: list[DataSeriesStatus] = Field(default_factory=list)
    market_series: list[DataSeriesStatus] = Field(default_factory=list)
    institutional_series: list[DataSeriesStatus] = Field(default_factory=list)
    branch_series: list[DataSeriesStatus] = Field(default_factory=list)


class Bar(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    trading_date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    adjusted_close: float | None = Field(default=None, gt=0)
    adjustment_factor: float = Field(default=1.0, gt=0)
    volume: int = Field(ge=0)
    available_at: datetime
    source: str
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_ohlc(self) -> Bar:
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be greater than or equal to open, close, and low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be less than or equal to open, close, and high")
        return self


class CorporateAction(BaseModel):
    """Official ex-right/ex-dividend reference-price event.

    ``previous_close / reference_price`` is accumulated from ``ex_date`` onward.
    This forward adjustment corrects the event discontinuity without restating
    observations before the event with information from the future.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    ex_date: date
    previous_close: float = Field(gt=0)
    reference_price: float = Field(gt=0)
    event_type: str
    available_at: datetime
    source: str
    revision: int = Field(default=1, ge=1)


class Alert(BaseModel):
    code: str
    severity: AlertSeverity
    title: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class InstitutionalFlow(BaseModel):
    """Daily stock-level institutional trading, available only after market close."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    trading_date: date
    foreign_buy: int = Field(ge=0)
    foreign_sell: int = Field(ge=0)
    foreign_net: int
    investment_trust_buy: int = Field(ge=0)
    investment_trust_sell: int = Field(ge=0)
    investment_trust_net: int
    dealer_buy: int = Field(ge=0)
    dealer_sell: int = Field(ge=0)
    dealer_net: int
    total_net: int
    foreign_holding_ratio: float | None = Field(default=None, ge=0, le=100)
    available_at: datetime
    source: str
    revision: int = Field(default=1, ge=1)


class BrokerBranchFlow(BaseModel):
    """Normalized licensed broker-branch daily flow record."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    trading_date: date
    branch_code: str
    branch_name: str
    buy_shares: int = Field(ge=0)
    sell_shares: int = Field(ge=0)
    buy_amount: float = Field(ge=0)
    sell_amount: float = Field(ge=0)
    day_trade_buy_shares: int | None = Field(default=None, ge=0)
    day_trade_sell_shares: int | None = Field(default=None, ge=0)
    available_at: datetime
    source: str
    revision: int = Field(default=1, ge=1)

    @property
    def net_shares(self) -> int:
        return self.buy_shares - self.sell_shares


class ChipFlowMetrics(BaseModel):
    foreign_net_5d: int
    investment_trust_net_5d: int
    dealer_net_5d: int
    total_net_20d: int
    flow_volume_ratio_5d: float
    foreign_streak: int
    investment_trust_streak: int
    foreign_holding_ratio: float | None = None
    foreign_holding_change_5d: float | None = None
    branch_buy_concentration_5d: float | None = None
    branch_sell_concentration_5d: float | None = None
    branch_estimated_cost: float | None = None
    branch_day_trade_ratio: float | None = None


class BranchRanking(BaseModel):
    branch_code: str
    branch_name: str
    net_shares: int
    estimated_cost: float | None = None
    win_rate: float | None = Field(default=None, ge=0, le=1)


class ChipFlowSnapshot(BaseModel):
    symbol: str
    as_of: date
    strategy_version: str = "chip_flow_v1"
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    institutional_observations: int = Field(ge=1)
    branch_observations: int = Field(ge=0)
    branch_data_available: bool
    branch_data_status: str
    metrics: ChipFlowMetrics
    latest: InstitutionalFlow
    top_buyers: list[BranchRanking] = Field(default_factory=list)
    top_sellers: list[BranchRanking] = Field(default_factory=list)
    alerts: list[Alert] = Field(default_factory=list)
    data_available_at: datetime


_TAIPEI = ZoneInfo("Asia/Taipei")


def taipei_day_end(day: date) -> datetime:
    """First instant after ``day`` in Asia/Taipei: the exclusive upper bound for as-of reads."""
    return datetime.combine(day + timedelta(days=1), time.min, _TAIPEI)


class NewsItem(BaseModel):
    """One classified headline.

    ``published_at`` is the display/recency time (the publisher's own clock).
    ``available_at`` is when this system first held the item: point-in-time reads
    must filter on it, because an item published on 11/20 but fetched on 11/26 was
    not knowable on 11/21. It defaults to ``retrieved_at``.
    """

    symbol: str
    headline: str
    url: str
    source: str
    published_at: datetime
    sentiment_score: float | None = Field(default=None, ge=-1, le=1)
    sentiment_label: Literal["bullish", "neutral", "bearish"] | None = None
    retrieved_at: datetime
    available_at: datetime | None = None
    # When the label was written; as-of reads before it see the item as unclassified.
    classified_at: datetime | None = None
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _default_available_at(self) -> NewsItem:
        if self.available_at is None:
            self.available_at = self.retrieved_at
        return self

    @property
    def short_id(self) -> str:
        """Stable 8-hex id for citing this item as ``news:<short_id>``."""
        digest = hashlib.sha1(f"{self.source}|{self.url}".encode()).hexdigest()
        return digest[:8]


def hide_late_label(item: NewsItem, cutoff: datetime) -> NewsItem:
    """As-of view of an item: a label written at or after ``cutoff`` was not yet known."""
    if item.classified_at is not None and item.classified_at >= cutoff:
        return item.model_copy(update={"sentiment_label": None, "sentiment_score": None})
    return item


class NewsHeadlineRanking(BaseModel):
    headline: str
    url: str
    source: str
    published_at: datetime
    sentiment_score: float | None = None
    sentiment_label: Literal["bullish", "neutral", "bearish"] | None = None


class NewsSentimentSnapshot(BaseModel):
    symbol: str
    as_of: date
    strategy_version: str = "news_sentiment_v1"
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    article_count: int = Field(ge=0)
    sentiment_label: Literal["bullish", "neutral", "bearish"]
    top_headlines: list[NewsHeadlineRanking] = Field(default_factory=list)
    data_available_at: datetime


class FactorScores(BaseModel):
    trend: float = Field(ge=0, le=100)
    momentum: float = Field(ge=0, le=100)
    volume: float = Field(ge=0, le=100)
    relative_strength: float = Field(ge=0, le=100)
    risk_quality: float = Field(ge=0, le=100)


class SignalSnapshot(BaseModel):
    symbol: str
    as_of: date
    strategy_version: str
    score: float = Field(ge=0, le=100)
    action: SignalAction
    regime: MarketRegime
    confidence: float = Field(ge=0, le=1)
    factors: FactorScores
    alerts: list[Alert] = Field(default_factory=list)
    data_available_at: datetime
    observations: int = Field(ge=1)


class MarketObservation(BaseModel):
    """One point-in-time daily snapshot of market breadth and capital conditions."""

    model_config = ConfigDict(frozen=True)

    market: str
    trading_date: date
    index_close: float = Field(gt=0)
    total_issues: int = Field(gt=0)
    advancing_issues: int = Field(ge=0)
    declining_issues: int = Field(ge=0)
    unchanged_issues: int = Field(ge=0)
    above_ma20_issues: int = Field(ge=0)
    above_ma60_issues: int = Field(ge=0)
    new_high_52w_issues: int = Field(ge=0)
    new_low_52w_issues: int = Field(ge=0)
    up_volume: float = Field(ge=0)
    down_volume: float = Field(ge=0)
    turnover_value: float = Field(gt=0)
    foreign_net_flow: float
    investment_trust_net_flow: float
    dealer_net_flow: float
    futures_net_open_interest: float
    # True when ``futures_net_open_interest`` is the real official position (which may be a
    # genuine 0); False means "no TAIFEX data stored for that date" (the column itself is
    # not nullable). Rows built without the flag keep the old meaning: 0 = unknown.
    futures_oi_known: bool = False
    margin_balance: float = Field(ge=0)
    short_balance: float = Field(ge=0)
    available_at: datetime
    source: str
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="before")
    @classmethod
    def default_futures_oi_known(cls, data: Any) -> Any:
        if isinstance(data, dict) and "futures_oi_known" not in data:
            return {**data, "futures_oi_known": data.get("futures_net_open_interest", 0) != 0}
        return data

    @model_validator(mode="after")
    def validate_issue_counts(self) -> MarketObservation:
        if (
            self.advancing_issues + self.declining_issues + self.unchanged_issues
            > self.total_issues
        ):
            raise ValueError("advance, decline, and unchanged counts exceed total issues")
        breadth_counts = (
            self.above_ma20_issues,
            self.above_ma60_issues,
            self.new_high_52w_issues,
            self.new_low_52w_issues,
        )
        if any(count > self.total_issues for count in breadth_counts):
            raise ValueError("breadth count exceeds total issues")
        return self


class MarketEnvironmentFactors(BaseModel):
    participation: float = Field(ge=0, le=100)
    advance_decline: float = Field(ge=0, le=100)
    breadth_momentum: float = Field(ge=0, le=100)
    volume_breadth: float = Field(ge=0, le=100)
    institutional_flow: float = Field(ge=0, le=100)
    leverage_quality: float = Field(ge=0, le=100)


class MarketEnvironmentSnapshot(BaseModel):
    market: str
    as_of: date
    strategy_version: str
    score: float = Field(ge=0, le=100)
    regime: MarketRegime
    confidence: float = Field(ge=0, le=1)
    factors: MarketEnvironmentFactors
    alerts: list[Alert] = Field(default_factory=list)
    latest: MarketObservation
    data_available_at: datetime
    observations: int = Field(ge=1)


class BacktestSpec(BaseModel):
    symbol: str
    benchmark: str | None = None
    market: str | None = None
    strategy_version: str = "regime_v1"
    start: date
    end: date
    initial_capital: float = Field(default=1_000_000, gt=0)
    entry_score: float = Field(default=70, ge=0, le=100)
    exit_score: float = Field(default=45, ge=0, le=100)
    # None for both fee_bps and sell_tax_bps (the default) selects the Taiwan cost model
    # (domain/tw_trading.py: 0.1425% commission with NT$20 minimum, sell tax 0.3% / ETF
    # 0.1%). Supplying either one keeps the legacy flat-bps model (missing one = 5 / 0).
    fee_bps: float | None = Field(default=None, ge=0, le=1000)
    slippage_bps: float = Field(default=5, ge=0, le=1000)
    sell_tax_bps: float | None = Field(default=None, ge=0, le=1000)
    commission_discount: float = Field(default=1.0, gt=0, le=1)
    # Append a provisional bar for today from quote_minutes when no official bar exists.
    include_live_today: bool = False
    use_chip_filter: bool = False
    chip_entry_score: float = Field(default=55, ge=0, le=100)
    chip_exit_score: float = Field(default=40, ge=0, le=100)

    @model_validator(mode="after")
    def validate_period_and_thresholds(self) -> BacktestSpec:
        if self.start >= self.end:
            raise ValueError("start must be before end")
        if self.exit_score >= self.entry_score:
            raise ValueError("exit_score must be lower than entry_score")
        if self.chip_exit_score >= self.chip_entry_score:
            raise ValueError("chip_exit_score must be lower than chip_entry_score")
        return self


class EquityPoint(BaseModel):
    trading_date: date
    equity: float
    position: float = Field(ge=0, le=1)


class BacktestTrade(BaseModel):
    trading_date: date
    side: Literal["buy", "sell"]
    price: float
    cost: float
    equity: float


class BacktestReport(BaseModel):
    spec: BacktestSpec
    started_at: datetime
    completed_at: datetime
    observations: int
    trades: int
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float | None
    max_drawdown: float
    equity_curve: list[EquityPoint]
    buy_hold_curve: list[EquityPoint] = Field(default_factory=list)
    buy_hold_return: float | None = None
    excess_return: float | None = None
    transactions: list[BacktestTrade] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    cost_model: Literal["tw", "custom"] = "custom"
    provisional_bar: bool = False
    as_of_time: datetime | None = None


class BacktestJob(BaseModel):
    id: UUID
    status: JobStatus
    spec: BacktestSpec
    result: BacktestReport | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AnalysisScenario(BaseModel):
    name: str
    condition: str
    response: str


class LLMJudgement(BaseModel):
    headline: str
    stance: Literal["bullish", "neutral", "cautious", "bearish"]
    operation_guide: str
    key_evidence: list[str] = Field(min_length=1, max_length=8)
    risk_warnings: list[str] = Field(default_factory=list, max_length=8)
    scenarios: list[AnalysisScenario] = Field(default_factory=list, max_length=5)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)


class LLMAnalysisContext(BaseModel):
    symbol_signal: SignalSnapshot
    market_environment: MarketEnvironmentSnapshot
    chip_flow: ChipFlowSnapshot | None = None
    news_sentiment: NewsSentimentSnapshot | None = None
    backtest: BacktestReport | None = None
    # Compact dated official-data blocks (see application/official_evidence.py); None = no data.
    valuation: dict[str, Any] | None = None
    revenue_trend: dict[str, Any] | None = None
    earnings: dict[str, Any] | None = None
    holders: dict[str, Any] | None = None
    short_interest: dict[str, Any] | None = None
    stock_futures: dict[str, Any] | None = None
    upcoming_events: dict[str, Any] | None = None
    market_derivatives: dict[str, Any] | None = None
    screener: dict[str, Any] | None = None


class LLMAnalysisReport(BaseModel):
    symbol: str
    as_of: date
    model: str
    prompt_version: str
    generated_at: datetime
    judgement: LLMJudgement
    context: LLMAnalysisContext


class DebateCase(BaseModel):
    stance: Literal["bullish", "bearish"]
    thesis: str
    key_evidence: list[str] = Field(min_length=1, max_length=8)
    counterpoints_to_address: list[str] = Field(default_factory=list, max_length=8)
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)


class DebateRiskReview(BaseModel):
    summary: str
    risk_warnings: list[str] = Field(default_factory=list, max_length=8)
    invalidation_conditions: list[str] = Field(default_factory=list, max_length=8)
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)


class DebateEvidence(BaseModel):
    id: str
    source: str
    as_of: date
    details: dict[str, Any]


class DebateAnalysisReport(BaseModel):
    symbol: str
    as_of: date
    model: str
    judge_model: str
    prompt_version: str
    generated_at: datetime
    bull_case: DebateCase
    bear_case: DebateCase
    judgement: LLMJudgement
    context: LLMAnalysisContext
    evidence: list[DebateEvidence] = Field(default_factory=list)
    risk_review: DebateRiskReview | None = None
    data_sufficient: bool = True


class ApiKey(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    created_at: datetime
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class User(BaseModel):
    id: UUID
    google_sub: str
    email: str
    name: str | None = None
    picture: str | None = None
    is_active: bool = True
    created_at: datetime
    last_login_at: datetime | None = None


class MarketBackfillStatus(BaseModel):
    """How far the whole-market history fill has got, for one exchange."""

    market: Literal["TW", "TWO"]
    state: Literal["queued", "running", "complete", "idle"]
    lookback_days: int
    days_total: int
    days_done: int
    percent: float
    rate_per_minute: float | None = None
    eta_seconds: int | None = None
    stored_sessions: int
    sessions_needed_for_score: int
    latest_score_date: date | None = None
    score_ready: bool


class StockDayPoint(BaseModel):
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    volume_lots: int
    turnover_value: float
    change_pct: float | None = None
    foreign_net_lots: float | None = None
    trust_net_lots: float | None = None
    dealer_net_lots: float | None = None
    total_net_lots: float | None = None
    margin_balance: int | None = None
    margin_change: int | None = None
    short_balance: int | None = None
    short_change: int | None = None


class StockSummary(BaseModel):
    last_close: float
    day_change_pct: float | None = None
    return_5d_pct: float | None = None
    return_20d_pct: float | None = None
    # Newest session whose institutional-flow report is out (quotes can be a day ahead).
    flows_through: date | None = None
    foreign_net_5d_lots: float | None = None
    total_net_5d_lots: float | None = None
    avg_volume_20d_lots: float | None = None


class PriceBar(BaseModel):
    """One daily candle for charting, on the price scale chosen by ``PriceSeries.adjust``."""

    trading_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class PriceSeries(BaseModel):
    """Daily candles for a chart.

    ``adjust="back"`` rescales history so the latest bar shows the real traded price and
    earlier bars are comparable across ex-right/ex-dividend dates. ``"none"`` returns
    the raw exchange prices. Only bars available on or before ``as_of`` are included.
    """

    symbol: str
    adjust: Literal["back", "none"]
    as_of: date | None
    bars: list[PriceBar]


class StockOverview(BaseModel):
    """A stock's recent quotes, flows and credit from the whole-market snapshot."""

    symbol: str
    name: str
    market: Literal["TW", "TWO"]
    data_through: date
    trading_days: int
    # "ready": adjusted history is stored. "preparing": a background sync is queued; the
    # snapshot view still works meanwhile. "insufficient": synced, but too few bars
    # (a fresh listing), so signals cannot be computed yet.
    full_analysis: Literal["ready", "preparing", "insufficient"]
    summary: StockSummary
    days: list[StockDayPoint]


class WatchlistItem(BaseModel):
    symbol: str
    name: str
    market: Literal["TW", "TWO"]
    added_at: datetime
    data_through: date | None = None
    close: float | None = None
    day_change_pct: float | None = None
    flows_through: date | None = None
    foreign_net_1d_lots: float | None = None
    total_net_5d_lots: float | None = None
    # Closing prices of the latest sessions (oldest first) for a sparkline; raw, unadjusted.
    recent_closes: list[float] = Field(default_factory=list)
    change_20d_pct: float | None = None
    full_analysis: Literal["ready", "preparing"] = "preparing"
    # Today's latest price when it is newer than the official close above (live tick
    # or the 10-minute whole-market snapshot). Percent units, like day_change_pct.
    latest_price: float | None = None
    latest_change_pct: float | None = None
    latest_at: datetime | None = None
    latest_source: Literal["tick", "snapshot"] | None = None


class Watchlist(BaseModel):
    limit: int
    count: int
    items: list[WatchlistItem]


SectorQuadrant = Literal["leading", "weakening", "lagging", "improving"]


class SectorReturn(BaseModel):
    equal_pct: float | None
    weighted_pct: float | None


class SectorRrgPoint(BaseModel):
    rs_ratio: float
    rs_momentum: float


class SectorRrg(BaseModel):
    rs_ratio: float
    rs_momentum: float
    quadrant: SectorQuadrant
    trail: list[SectorRrgPoint]


class SectorRow(BaseModel):
    code: str
    name: str
    stock_count: int
    returns: dict[str, SectorReturn]
    turnover_value: float
    turnover_share: float
    turnover_share_avg20: float | None
    turnover_share_change: float | None
    up_count: int
    down_count: int
    flat_count: int
    breadth_up_pct: float | None
    institutional_net_lots: float | None
    institutional_net_value: float | None
    rrg: SectorRrg | None


class SectorRotation(BaseModel):
    window: int
    as_of: date | None
    mode: Literal["close", "intraday"]
    # "live": serving intraday snapshots; "unavailable": inside the session but no
    # usable snapshots; "not_applicable": historical as-of or outside the session.
    intraday_status: Literal["live", "unavailable", "not_applicable"]
    data_through: date | None
    data_time: datetime | None
    trading_days: int
    institutional_through: date | None
    unclassified_count: int
    classification_ready: bool = True
    market_turnover_value: float
    market_return_equal_pct: dict[str, float | None]
    backfill_pending: bool = False
    sectors: list[SectorRow]


class SectorStockEntry(BaseModel):
    symbol: str
    name: str
    market: Literal["TW", "TWO"]
    close: float
    day_change_pct: float | None
    window_return_pct: float | None
    volume_lots: int
    turnover_value: float
    institutional_net_lots: float | None


class SectorStocks(BaseModel):
    code: str
    name: str
    window: int
    mode: Literal["close", "intraday"]
    data_through: date | None
    data_time: datetime | None
    backfill_pending: bool = False
    entries: list[SectorStockEntry]


# --- Official structured events (TWSE / TPEx OpenAPI) ---------------------------------
# Point in time: ``available_at`` is when *we* fetched the row, never the publication
# date. A changed row is stored as a new ``revision`` so "what did we know on date D"
# stays answerable.

MarketEventKind = Literal["material", "ex_rights"]
RiskFlagKind = Literal["disposition", "attention"]
EventCategory = Literal[
    "buyback",
    "capital_increase",
    "m_and_a",
    "major_contract",
    "guidance",
    "clarification",
    "litigation",
    "dividend",
    "executive_change",
    "other",
]
EventPolarity = Literal["positive", "negative", "neutral"]


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


class MarketEvent(BaseModel):
    """A 重大訊息 (material announcement) or an ex-rights/dividend schedule entry."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    kind: MarketEventKind
    external_key: str = Field(max_length=64)
    announced_at: datetime
    title: str
    clause: str | None = None
    detail: str | None = None
    effective_date: date | None = None
    source: str
    available_at: datetime
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _aware(self) -> MarketEvent:
        _require_aware(self.announced_at)
        _require_aware(self.available_at)
        return self


class RiskFlag(BaseModel):
    """A 處置股 (disposition) or 注意股 (attention) designation."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    kind: RiskFlagKind
    start_date: date
    end_date: date | None = None
    reason: str
    source: str
    available_at: datetime
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _aware(self) -> RiskFlag:
        _require_aware(self.available_at)
        return self


class MonthlyRevenue(BaseModel):
    """One company's monthly revenue. Amounts are NT$ thousands (仟元) as published."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    revenue: float
    mom_pct: float | None = None
    yoy_pct: float | None = None
    cumulative_revenue: float | None = None
    cumulative_yoy_pct: float | None = None
    source: str
    available_at: datetime
    revision: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _aware(self) -> MonthlyRevenue:
        _require_aware(self.available_at)
        return self


class RevenueMetrics(BaseModel):
    symbol: str
    period: str
    revenue: float
    yoy_pct: float | None
    mom_pct: float | None
    cumulative_yoy_pct: float | None
    # This month's YoY minus the mean YoY of the three preceding months.
    acceleration_pct: float | None
    consecutive_positive_yoy: int
    # None while fewer than 12 months of history have accumulated.
    is_12m_high: bool | None
    months_available: int
    available_at: datetime


class ClassifiedEvent(BaseModel):
    symbol: str
    kind: MarketEventKind
    announced_at: datetime
    title: str
    clause: str | None
    detail: str | None
    effective_date: date | None
    source: str
    category: EventCategory
    polarity: EventPolarity
    available_at: datetime


class StockEvents(BaseModel):
    symbol: str
    as_of: date
    days: int
    events: list[ClassifiedEvent]
    risk_flags: list[RiskFlag]
    revenue: RevenueMetrics | None
    note: str


class MarketRiskFlags(BaseModel):
    as_of: date
    count: int
    flags: list[RiskFlag]


class MarketFactorSnapshot(BaseModel):
    """One stock's multi-factor row for a trading date (see ``quant/screener.py``).

    ``metrics`` is an open dict so new factors can be added without a migration.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    trading_date: date
    version: str
    name: str
    market: Literal["TW", "TWO"]
    industry_code: str | None = None
    industry_name: str | None = None
    close: float
    change_pct: float | None = None
    avg_turnover_20d: float | None = None
    composite: float | None = None
    scores: dict[str, float | None] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    highlights: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    flow_through: date | None = None
    available_at: datetime


class MarketSession(BaseModel):
    """One market's trading date as seen in ``market_stock_daily`` (screener bookkeeping)."""

    model_config = ConfigDict(frozen=True)

    trading_date: date
    rows: int
    reported: bool  # the institutional report for the session is published
    available_at: datetime


class MarketFactorState(BaseModel):
    """What a stored factor snapshot was built from: per-market row counts and flow dates."""

    model_config = ConfigDict(frozen=True)

    available_at: datetime
    rows: dict[str, int]
    flow_through: dict[str, date | None]


class ShareholdingTier(BaseModel):
    """One TDCC holding tier (持股分級 1-16) as published: holders, shares, % of custody."""

    model_config = ConfigDict(frozen=True)

    level: int = Field(ge=1, le=16)
    holders: int
    shares: int
    pct: float


class ShareholdingSnapshot(BaseModel):
    """Lean weekly TDCC concentration numbers (no raw tiers), for whole-market reads.

    ``*_ge400`` means holders above 400,000 shares (TDCC levels 12-15, i.e. more than
    400 lots); ``*_ge1000`` is level 15 only (more than 1,000,000 shares); ``retail_*``
    is levels 1-3 (10,000 shares / 10 lots or fewer). Percentages are of the shares
    held in TDCC custody, computed from share counts.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    data_date: date
    total_holders: int
    total_shares: int
    holders_ge400: int
    pct_ge400: float
    holders_ge1000: int
    pct_ge1000: float
    retail_holders: int
    retail_pct: float
    source: str
    available_at: datetime

    @model_validator(mode="after")
    def _aware(self) -> ShareholdingSnapshot:
        _require_aware(self.available_at)
        return self


class ShareholdingDistribution(ShareholdingSnapshot):
    """A weekly TDCC shareholding distribution with the raw tiers kept."""

    tiers: list[ShareholdingTier] = Field(default_factory=list)

    def snapshot(self) -> ShareholdingSnapshot:
        return ShareholdingSnapshot.model_validate(
            {name: getattr(self, name) for name in ShareholdingSnapshot.model_fields}
        )


class ShareholdingPair(BaseModel):
    """Latest weekly row plus the comparison row (>= 4 weeks older, else the previous)."""

    model_config = ConfigDict(frozen=True)

    latest: ShareholdingSnapshot
    prior: ShareholdingSnapshot | None = None


class ForeignHolding(BaseModel):
    """Shares outstanding and foreign (外資及陸資) holding for one stock and day."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    trading_date: date
    shares_outstanding: int
    foreign_shares: int
    foreign_holding_pct: float
    source: str
    available_at: datetime

    @model_validator(mode="after")
    def _aware(self) -> ForeignHolding:
        _require_aware(self.available_at)
        return self


class HolderPoint(BaseModel):
    data_date: date
    pct_ge400: float
    pct_ge1000: float
    holders_ge400: int
    holders_ge1000: int
    retail_holders: int
    retail_pct: float
    total_holders: int
    total_shares: int
    available_at: datetime


class StockHolders(BaseModel):
    symbol: str
    as_of: date
    series: list[HolderPoint]
    shares_outstanding: int | None = None
    foreign_shares: int | None = None
    foreign_holding_pct: float | None = None
    foreign_data_date: date | None = None
    note: str
