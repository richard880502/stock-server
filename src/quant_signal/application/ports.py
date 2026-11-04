from __future__ import annotations

from datetime import date, datetime
from typing import Protocol
from uuid import UUID

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
    InstitutionalFlow,
    Instrument,
    MarketDataStatus,
    MarketEnvironmentSnapshot,
    MarketFlowDay,
    MarketObservation,
    MarketStockDay,
    NewsItem,
    SignalSnapshot,
    SyncJob,
    SyncJobKind,
    User,
)


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

    async def market_data_status(self) -> list[MarketDataStatus]: ...

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
