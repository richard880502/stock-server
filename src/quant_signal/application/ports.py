from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from quant_signal.domain.derivatives import (
    DerivativesRows,
    MarketDerivativesLatest,
    StockFuturesDay,
    StockFuturesMetrics,
)
from quant_signal.domain.etf import EtfDay
from quant_signal.domain.feed_stats import FeedDayStat
from quant_signal.domain.fundamentals import (
    EarningsMetrics,
    QuarterlyFinancial,
    RevenueTrend,
    ValuationDay,
    ValuationMetrics,
)
from quant_signal.domain.models import (
    ApiKey,
    BacktestJob,
    BacktestReport,
    BacktestSpec,
    Bar,
    BrokerBranchFlow,
    ChipFlowSnapshot,
    DataStatus,
    FetchCoverage,
    ForeignHolding,
    InstitutionalFlow,
    Instrument,
    IntradaySnapshot,
    MarketDataStatus,
    MarketDataVersion,
    MarketEnvironmentSnapshot,
    MarketEvent,
    MarketFactorSnapshot,
    MarketFactorState,
    MarketFlowDay,
    MarketIndustry,
    MarketObservation,
    MarketRankingRow,
    MarketSession,
    MarketStockDay,
    MonthlyRevenue,
    NewsItem,
    RiskFlag,
    ShareholdingDistribution,
    ShareholdingPair,
    SignalSnapshot,
    SyncJob,
    SyncJobKind,
    User,
)
from quant_signal.infrastructure.db.prices import LatestQuote, MinuteBar


class QuantRepository(Protocol):
    async def get_data_status(self) -> DataStatus: ...

    async def list_bars(
        self,
        symbol: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[Bar]: ...

    async def save_signal(self, snapshot: SignalSnapshot) -> None: ...

    async def list_institutional_flows(
        self,
        symbol: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[InstitutionalFlow]: ...

    async def list_broker_branch_flows(
        self,
        symbol: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[BrokerBranchFlow]: ...

    async def save_chip_flow(self, snapshot: ChipFlowSnapshot) -> None: ...

    async def list_market_observations(
        self,
        market: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[MarketObservation]: ...

    async def save_market_environment(
        self,
        snapshot: MarketEnvironmentSnapshot,
    ) -> None: ...

    async def create_backtest_job(self, spec: BacktestSpec) -> BacktestJob: ...

    async def get_backtest_job(self, job_id: UUID) -> BacktestJob | None: ...

    async def claim_next_backtest_job(self) -> BacktestJob | None: ...

    async def complete_backtest_job(
        self,
        job_id: UUID,
        report: BacktestReport,
    ) -> None: ...

    async def fail_backtest_job(self, job_id: UUID, error: str) -> None: ...

    async def create_api_key(
        self,
        *,
        name: str,
        key_hash: str,
        key_prefix: str,
    ) -> ApiKey: ...

    async def get_api_key_by_hash(self, key_hash: str) -> ApiKey | None: ...

    async def list_api_keys(self) -> list[ApiKey]: ...

    async def revoke_api_key(self, key_id: UUID) -> bool: ...

    async def touch_api_key_last_used(self, key_id: UUID) -> None: ...

    async def search_instruments(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[Instrument]: ...

    async def list_news_items(
        self,
        symbol: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[NewsItem]: ...

    async def instrument_exists(self, symbol: str) -> bool: ...

    async def list_watchlist_symbols(self) -> list[str]: ...

    async def upsert_news_items(self, items: list[NewsItem]) -> int: ...

    async def list_fetch_coverage(
        self,
        source: str,
        dataset: str,
        symbol: str,
        periods: list[str],
    ) -> dict[str, FetchCoverage]: ...

    async def record_fetch_coverage(self, entries: list[FetchCoverage]) -> None: ...

    async def enqueue_sync_job(
        self,
        kind: SyncJobKind,
        symbol: str,
        *,
        lookback_days: int = 500,
    ) -> tuple[SyncJob, bool]: ...

    async def claim_next_sync_job(self) -> SyncJob | None: ...

    async def complete_sync_job(self, job_id: UUID) -> None: ...

    async def fail_sync_job(self, job_id: UUID, error: str, *, retry: bool) -> None: ...

    async def requeue_stale_sync_jobs(self, *, older_than_seconds: float) -> int: ...

    async def list_tracked_symbols(self) -> list[str]: ...

    async def upsert_market_stock_days(self, rows: list[MarketStockDay]) -> int: ...

    async def list_market_trading_dates(
        self,
        market: str | None,
        *,
        as_of: date | None,
        limit: int,
        require: str | None = None,
    ) -> list[date]: ...

    async def list_market_stock_days(
        self,
        dates: list[date],
        market: str | None,
        *,
        as_of: date | None = None,
    ) -> list[MarketStockDay]: ...

    async def market_flow_totals(
        self,
        dates: list[date],
        market: str | None,
        *,
        as_of: date | None = None,
    ) -> list[MarketFlowDay]: ...

    async def market_ranking(
        self,
        metric: str,
        dates: list[date],
        market: str | None,
        *,
        as_of: date | None,
        order: Literal["desc", "asc"],
        limit: int,
    ) -> list[MarketRankingRow]:
        """Top ``limit`` stocks by ``metric`` over ``dates``, aggregated by the store.

        Only stocks with a row on the newest of ``dates`` are ranked; ties sort by symbol.
        """
        ...

    async def list_market_sector_days(
        self, dates: list[date], *, as_of: date | None = None
    ) -> list[tuple[Any, ...]]:
        """Lean rows ``(symbol, trading_date, name, market, close, change, volume,
        turnover_value, total_net, available_at)`` for sector rotation (whole market)."""
        ...

    async def market_data_version(self) -> MarketDataVersion:
        """A cheap fingerprint of the whole-market tables; it changes when a sync writes."""
        ...

    cache_namespace: str

    async def market_data_status(self) -> list[MarketDataStatus]: ...

    async def market_session_summary(self, market: str) -> tuple[int, date | None]:
        """``(number of stored sessions, newest session)`` of one market."""
        ...

    async def latest_market_observation_date(self, market: str, source: str) -> date | None: ...

    async def upsert_market_industries(self, rows: list[MarketIndustry]) -> int: ...

    async def list_market_industries(self) -> list[MarketIndustry]: ...

    async def upsert_market_events(self, rows: list[MarketEvent]) -> int: ...

    async def upsert_risk_flags(self, rows: list[RiskFlag]) -> int: ...

    async def upsert_monthly_revenue(self, rows: list[MonthlyRevenue]) -> int: ...

    async def list_market_events(
        self, symbols: list[str] | None, since: datetime, as_of: datetime
    ) -> list[MarketEvent]: ...

    async def active_risk_flags(
        self, as_of: datetime, symbols: list[str] | None = None
    ) -> list[RiskFlag]: ...

    async def monthly_revenue(
        self, symbol: str, as_of: datetime, months: int = 13
    ) -> list[MonthlyRevenue]: ...

    async def latest_monthly_revenue(self, as_of: datetime) -> list[MonthlyRevenue]: ...

    async def upsert_shareholding_distribution(
        self, rows: list[ShareholdingDistribution]
    ) -> int: ...

    async def upsert_foreign_holding(self, rows: list[ForeignHolding]) -> int: ...

    async def latest_shareholding_pairs(self, as_of: datetime) -> dict[str, ShareholdingPair]: ...

    async def latest_foreign_holdings(self, as_of: datetime) -> dict[str, ForeignHolding]: ...

    async def latest_foreign_holding(
        self, symbol: str, as_of: datetime
    ) -> ForeignHolding | None: ...

    async def shareholding_history(
        self, symbol: str, as_of: datetime, weeks: int = 26
    ) -> list[ShareholdingDistribution]: ...

    async def upsert_valuation_daily(self, rows: list[ValuationDay]) -> int: ...

    async def latest_valuation_metrics(
        self, as_of: datetime, symbols: list[str] | None = None, *, window: int = 250
    ) -> dict[str, ValuationMetrics]: ...

    async def valuation_history(
        self, symbol: str, as_of: datetime, sessions: int = 300
    ) -> list[ValuationDay]: ...

    async def insert_missing_monthly_revenue(self, rows: list[MonthlyRevenue]) -> int: ...

    async def revenue_trends(
        self, as_of: datetime, symbols: list[str] | None = None, *, include_months: bool = False
    ) -> dict[str, RevenueTrend]: ...

    async def upsert_quarterly_financials(self, rows: list[QuarterlyFinancial]) -> int: ...

    async def insert_missing_quarterly_financials(self, rows: list[QuarterlyFinancial]) -> int: ...

    async def quarterly_financials(
        self, symbol: str, as_of: datetime
    ) -> list[QuarterlyFinancial]: ...

    async def earnings_summaries(
        self, as_of: datetime, symbols: list[str] | None = None
    ) -> dict[str, EarningsMetrics]: ...
    # TAIFEX derivatives (see ``infrastructure/derivatives_queries.py``).
    async def derivatives_rows(self, as_of: datetime, days: int) -> DerivativesRows: ...

    async def latest_market_derivatives(
        self, as_of: datetime
    ) -> MarketDerivativesLatest | None: ...

    async def latest_stock_futures_metrics(
        self, as_of: datetime
    ) -> dict[str, StockFuturesMetrics]: ...

    async def stock_futures_history(
        self, symbol: str, as_of: datetime, days: int
    ) -> list[StockFuturesDay]: ...

    async def has_stock_futures(self, symbol: str) -> bool: ...

    async def stock_spot_closes(self, symbol: str, start: date, end: date) -> dict[date, float]: ...

    async def foreign_tx_equiv_net_oi_on(self, day: date) -> float | None: ...

    async def latest_quotes(self, symbols: list[str]) -> dict[str, LatestQuote]: ...

    async def list_minute_bars(
        self, symbol: str, *, since: datetime, until: datetime
    ) -> list[MinuteBar]: ...

    async def latest_intraday_snapshots(
        self, *, day: date, now: datetime
    ) -> list[IntradaySnapshot]: ...

    async def list_market_closes(
        self, dates: list[date], market: str
    ) -> list[tuple[str, date, float, float, int]]: ...

    async def has_active_sync_job(self, kind: SyncJobKind) -> bool: ...

    async def list_active_sync_jobs(self, kind: SyncJobKind) -> list[SyncJob]: ...

    async def upsert_google_user(
        self,
        *,
        google_sub: str,
        email: str,
        name: str | None,
        picture: str | None,
    ) -> User: ...

    async def create_user_session(
        self, *, user_id: UUID, token_hash: str, expires_at: datetime
    ) -> None: ...

    async def get_user_by_session(self, token_hash: str, *, now: datetime) -> User | None: ...

    async def delete_user_session(self, token_hash: str) -> None: ...

    async def consume_usage(
        self, user_id: UUID, action: str, day: date, *, limit: int
    ) -> tuple[bool, int]: ...

    async def search_market_stocks(self, query: str, *, limit: int = 10) -> list[Instrument]: ...

    async def upsert_etf_days(self, rows: list[EtfDay]) -> int: ...

    async def list_etf_rows(
        self, symbols: list[str], *, as_of: date | None = None, sessions: int = 3
    ) -> list[EtfDay]: ...

    async def search_etf_instruments(self, query: str, *, limit: int = 10) -> list[Instrument]: ...

    async def list_market_stock_rows(
        self, symbols: list[str], dates: list[date]
    ) -> list[MarketStockDay]: ...

    async def list_watchlist(self, user_id: UUID) -> list[tuple[str, str, str, datetime]]: ...

    async def add_watchlist_item(
        self, user_id: UUID, *, symbol: str, name: str, market: str, limit: int
    ) -> Literal["added", "exists", "full"]: ...

    async def remove_watchlist_item(self, user_id: UUID, symbol: str) -> bool: ...

    async def market_flow_through(self, end: date) -> dict[str, date]: ...

    def iter_market_histories(
        self, end: date, sessions: int
    ) -> AsyncIterator[tuple[str, str, str, list[tuple[Any, ...]]]]: ...

    async def feed_day_stats(self, start: date, end: date) -> dict[str, list[FeedDayStat]]: ...

    async def market_session_calendar(
        self, end: date, sessions: int
    ) -> dict[str, list[MarketSession]]: ...

    async def market_factor_state(
        self, version: str, dates: list[date]
    ) -> dict[date, MarketFactorState]: ...

    async def delete_market_factors(self, trading_date: date, version: str) -> int: ...

    async def replace_market_factors(
        self, trading_date: date, version: str, rows: list[MarketFactorSnapshot]
    ) -> int: ...

    async def prune_market_factors(self, version: str, keep: int) -> int: ...

    async def delete_other_factor_versions(self, keep_version: str) -> int: ...

    async def latest_market_factor_date(
        self, version: str, as_of: date | None = None
    ) -> date | None: ...

    async def list_market_factors(
        self, trading_date: date, version: str
    ) -> list[MarketFactorSnapshot]: ...
