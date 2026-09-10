from __future__ import annotations

from datetime import date
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
    InstitutionalFlow,
    MarketEnvironmentSnapshot,
    MarketObservation,
    SignalSnapshot,
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
