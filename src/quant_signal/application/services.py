from __future__ import annotations

from datetime import date
from uuid import UUID

from quant_signal.application.ports import QuantRepository
from quant_signal.domain.models import (
    BacktestJob,
    BacktestSpec,
    ChipFlowSnapshot,
    DataStatus,
    Instrument,
    MarketEnvironmentSnapshot,
    SignalSnapshot,
)
from quant_signal.quant.chip_flow import ChipFlowEngine
from quant_signal.quant.engine import QuantSignalEngine
from quant_signal.quant.market_environment import MarketEnvironmentEngine


class SignalService:
    def __init__(
        self,
        repository: QuantRepository,
        engine: QuantSignalEngine | None = None,
    ) -> None:
        self.repository = repository
        self.engine = engine or QuantSignalEngine()

    async def analyze(
        self,
        symbol: str,
        *,
        as_of: date,
        strategy_version: str = "regime_v1",
        benchmark: str | None = None,
        persist: bool = True,
    ) -> SignalSnapshot:
        bars = await self.repository.list_bars(symbol, end=as_of)
        benchmark_bars = (
            await self.repository.list_bars(benchmark, end=as_of) if benchmark else None
        )
        snapshot = self.engine.analyze(
            bars,
            as_of=as_of,
            strategy_version=strategy_version,
            benchmark_bars=benchmark_bars,
        )
        if persist:
            await self.repository.save_signal(snapshot)
        return snapshot


class DataStatusService:
    def __init__(self, repository: QuantRepository) -> None:
        self.repository = repository

    async def get(self) -> DataStatus:
        return await self.repository.get_data_status()


class InstrumentSearchService:
    def __init__(self, repository: QuantRepository) -> None:
        self.repository = repository

    async def search(self, query: str, *, limit: int = 10) -> list[Instrument]:
        return await self.repository.search_instruments(query, limit=limit)


class ChipFlowService:
    def __init__(
        self,
        repository: QuantRepository,
        engine: ChipFlowEngine | None = None,
    ) -> None:
        self.repository = repository
        self.engine = engine or ChipFlowEngine()

    async def analyze(
        self,
        symbol: str,
        *,
        as_of: date,
        strategy_version: str = "chip_flow_v1",
        persist: bool = True,
    ) -> ChipFlowSnapshot:
        flows = await self.repository.list_institutional_flows(symbol, end=as_of)
        branches = await self.repository.list_broker_branch_flows(symbol, end=as_of)
        bars = await self.repository.list_bars(symbol, end=as_of)
        snapshot = self.engine.analyze(
            flows,
            as_of=as_of,
            bars=bars,
            branch_flows=branches,
            strategy_version=strategy_version,
        )
        if persist:
            await self.repository.save_chip_flow(snapshot)
        return snapshot


class MarketEnvironmentService:
    def __init__(
        self,
        repository: QuantRepository,
        engine: MarketEnvironmentEngine | None = None,
    ) -> None:
        self.repository = repository
        self.engine = engine or MarketEnvironmentEngine()

    async def analyze(
        self,
        market: str,
        *,
        as_of: date,
        strategy_version: str = "market_env_v1",
        persist: bool = True,
    ) -> MarketEnvironmentSnapshot:
        observations = await self.repository.list_market_observations(
            market,
            end=as_of,
        )
        snapshot = self.engine.analyze(
            observations,
            as_of=as_of,
            strategy_version=strategy_version,
        )
        if persist:
            await self.repository.save_market_environment(snapshot)
        return snapshot


class BacktestJobService:
    def __init__(self, repository: QuantRepository) -> None:
        self.repository = repository

    async def start(self, spec: BacktestSpec) -> BacktestJob:
        return await self.repository.create_backtest_job(spec)

    async def get(self, job_id: UUID) -> BacktestJob | None:
        return await self.repository.get_backtest_job(job_id)
