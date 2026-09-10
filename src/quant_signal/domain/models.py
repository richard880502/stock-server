from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

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


class DataSeriesStatus(BaseModel):
    key: str
    source: str
    first_date: date
    latest_date: date
    observations: int = Field(ge=0)
    adjusted_observations: int = Field(default=0, ge=0)
    is_real: bool


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
    margin_balance: float = Field(ge=0)
    short_balance: float = Field(ge=0)
    available_at: datetime
    source: str
    revision: int = Field(default=1, ge=1)

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
    fee_bps: float = Field(default=5, ge=0)
    slippage_bps: float = Field(default=5, ge=0)
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


class LLMAnalysisContext(BaseModel):
    symbol_signal: SignalSnapshot
    market_environment: MarketEnvironmentSnapshot
    chip_flow: ChipFlowSnapshot | None = None
    backtest: BacktestReport | None = None


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


class DebateAnalysisReport(BaseModel):
    symbol: str
    as_of: date
    model: str
    prompt_version: str
    generated_at: datetime
    bull_case: DebateCase
    bear_case: DebateCase
    judgement: LLMJudgement
    context: LLMAnalysisContext


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
