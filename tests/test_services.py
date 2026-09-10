from __future__ import annotations

from quant_signal.application.services import (
    BacktestJobService,
    MarketEnvironmentService,
    SignalService,
)
from quant_signal.domain.models import (
    BacktestSpec,
    Bar,
    JobStatus,
    MarketObservation,
)
from quant_signal.infrastructure.memory import MemoryQuantRepository


async def test_signal_service_persists_snapshot(trending_bars: list[Bar]) -> None:
    repository = MemoryQuantRepository()
    await repository.upsert_bars(trending_bars)

    result = await SignalService(repository).analyze(
        "TEST",
        as_of=trending_bars[-1].trading_date,
    )

    assert repository.signals == [result]


async def test_backtest_job_is_durable_in_repository(
    trending_bars: list[Bar],
) -> None:
    repository = MemoryQuantRepository()
    service = BacktestJobService(repository)
    spec = BacktestSpec(
        symbol="TEST",
        start=trending_bars[80].trading_date,
        end=trending_bars[-1].trading_date,
    )

    created = await service.start(spec)
    fetched = await service.get(created.id)

    assert fetched is not None
    assert fetched.status == JobStatus.QUEUED
    assert fetched.spec == spec


async def test_market_environment_service_persists_snapshot(
    healthy_market: list[MarketObservation],
) -> None:
    repository = MemoryQuantRepository()
    await repository.upsert_market_observations(healthy_market)

    result = await MarketEnvironmentService(repository).analyze(
        "TW",
        as_of=healthy_market[-1].trading_date,
    )

    assert repository.market_environments == [result]
