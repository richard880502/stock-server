"""Valuation, monthly-revenue trend and quarterly-earnings models.

Units (explicit on purpose):

- ratios (P/E, P/B) are plain multiples; ``dividend_yield_pct`` and every ``*_pct`` are
  percent (5.0 = 5%); percentiles are 0-100.
- statement amounts are NT$ thousands (仟元) as published; EPS is NT$ per share.
- Taiwan income statements are *year-to-date cumulative* (Q3 holds Jan-Sep); a stored
  ``QuarterlyFinancial`` keeps exactly what the source gave (``cumulative=True``) and
  ``quant/quarterly.py`` derives single-quarter values.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quant_signal.domain.models import _require_aware

PERCENTILE_WINDOW = 250  # sessions
MIN_PERCENTILE_OBSERVATIONS = 60


class ValuationDay(BaseModel):
    """One stock's official P/E, P/B and dividend yield for a trading day.

    ``pe_ratio`` is None for loss-making companies (the exchanges print ``-``/``N/A``).
    ``dividend_year`` is the 股利年度 the yield is based on (ROC year converted to
    Gregorian); ``fiscal_period`` is the latest reported quarter behind P/E and P/B,
    e.g. ``2026Q3``.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    trading_date: date
    source: str
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    dividend_yield_pct: float | None = None
    dividend_year: int | None = None
    fiscal_period: str | None = None
    available_at: datetime

    @model_validator(mode="after")
    def _aware(self) -> ValuationDay:
        _require_aware(self.available_at)
        return self


class ValuationMetrics(BaseModel):
    """Latest valuation of a stock and where it sits in its own trailing history.

    ``*_percentile_250d``: share (0-100) of the stock's observations among its newest 250
    sessions (up to ``trading_date``) that are <= today's value, so 95 means richer than
    95% of its own recent history (for yield: higher yield). None with fewer than 60
    observations of that metric, or when today's value is missing.
    """

    symbol: str
    trading_date: date
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    dividend_yield_pct: float | None = None
    dividend_year: int | None = None
    fiscal_period: str | None = None
    pe_percentile_250d: float | None = None
    pb_percentile_250d: float | None = None
    yield_percentile_250d: float | None = None
    pe_observations: int = 0
    pb_observations: int = 0
    yield_observations: int = 0
    # valuation days visible among the newest 250 sessions (any metric), for "23 / 60 days"
    observations: int = 0


class ValuationPoint(BaseModel):
    date: date
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    dividend_yield_pct: float | None = None


class MonthRevenuePoint(BaseModel):
    period: str
    revenue: float  # NT$ thousands
    yoy_pct: float | None = None
    mom_pct: float | None = None


class RevenueTrend(BaseModel):
    """Revenue trend of one company, as of a cutoff (months oldest first)."""

    symbol: str
    latest_period: str
    months: list[MonthRevenuePoint] = Field(default_factory=list)
    yoy_3m_pct: float | None = None  # sum of last 3 months vs the same 3 months a year ago
    yoy_12m_pct: float | None = None  # sum of last 12 months vs the 12 before
    is_12m_high: bool | None = None  # needs 12 consecutive months
    is_history_high: bool | None = None  # latest is the max of every month we hold
    history_months: int = 0
    growth_streak_months: int = 0  # consecutive months (ending latest) with YoY > 0


class QuarterlyFinancial(BaseModel):
    """One company's statement for a fiscal year/quarter, exactly as published.

    Income lines are year-to-date cumulative (``cumulative=True``); ``equity_parent`` and
    ``total_assets`` are period-end balances. ``statement_kind='financial'`` (banks,
    holding companies, insurers, brokers, 異業) has no gross profit / operating income /
    comparable revenue, so those stay None -- only net income, EPS, equity and assets.
    All amounts NT$ thousands; ``eps`` NT$ per share (cumulative too).
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    fiscal_year: int
    fiscal_quarter: int = Field(ge=1, le=4)
    revision: int = Field(default=1, ge=1)
    statement_kind: Literal["general", "financial"] = "general"
    cumulative: bool = True
    revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income_parent: float | None = None
    eps: float | None = None
    equity_parent: float | None = None
    total_assets: float | None = None
    source: str
    available_at: datetime

    @model_validator(mode="after")
    def _aware(self) -> QuarterlyFinancial:
        _require_aware(self.available_at)
        return self


class QuarterSingle(BaseModel):
    """A single quarter derived from the cumulative statements (None where not derivable)."""

    symbol: str
    fiscal_year: int
    fiscal_quarter: int
    statement_kind: str
    revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income_parent: float | None = None
    eps: float | None = None
    equity_parent: float | None = None
    total_assets: float | None = None
    available_at: datetime

    @property
    def period(self) -> str:
        return f"{self.fiscal_year}Q{self.fiscal_quarter}"


class QuarterPoint(BaseModel):
    period: str
    revenue: float | None = None
    gross_margin_pct: float | None = None
    operating_margin_pct: float | None = None
    net_margin_pct: float | None = None
    eps: float | None = None


class EarningsMetrics(BaseModel):
    """Earnings summary of the newest visible quarter.

    ``eps_ttm`` / ``net_income_ttm`` are the trailing four quarters (current YTD + last
    fiscal year - same YTD a year earlier). ``*_yoy_pp`` are percentage-point changes of
    the single-quarter margin against the same quarter a year earlier. ``roe_ttm_pct`` is
    TTM net income attributable to owners over the latest period-end owners' equity (not
    an average), None when either is missing or equity <= 0.
    """

    symbol: str
    period: str  # e.g. "2026Q3"
    statement_kind: str
    revenue: float | None = None
    eps: float | None = None  # the latest single quarter
    eps_ttm: float | None = None
    eps_yoy_pct: float | None = None
    gross_margin_pct: float | None = None
    operating_margin_pct: float | None = None
    net_margin_pct: float | None = None
    gross_margin_yoy_pp: float | None = None
    operating_margin_yoy_pp: float | None = None
    net_margin_yoy_pp: float | None = None
    net_income_ttm: float | None = None
    equity_parent: float | None = None
    roe_ttm_pct: float | None = None
    available_at: datetime


class ValuationSection(BaseModel):
    date: date
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    dividend_yield_pct: float | None = None
    pe_percentile_250d: float | None = None
    pb_percentile_250d: float | None = None
    yield_percentile_250d: float | None = None
    # valuation days visible as of the cutoff within the newest 250 sessions, and how many a
    # percentile needs (``MIN_PERCENTILE_OBSERVATIONS``), so the UI can say "23 / 60 days"
    observations: int = 0
    observations_required: int = MIN_PERCENTILE_OBSERVATIONS
    history: list[ValuationPoint] = Field(default_factory=list)


class RevenueSection(BaseModel):
    months: list[MonthRevenuePoint]
    yoy_3m_pct: float | None = None
    yoy_12m_pct: float | None = None
    is_12m_high: bool | None = None
    growth_streak_months: int = 0


class EarningsSection(BaseModel):
    quarters: list[QuarterPoint]
    eps_ttm: float | None = None
    eps_yoy_pct: float | None = None
    roe_ttm_pct: float | None = None


class StockFundamentals(BaseModel):
    symbol: str
    as_of: date
    valuation: ValuationSection | None = None
    revenue: RevenueSection | None = None
    earnings: EarningsSection | None = None
    notes: list[str] = Field(default_factory=list)
