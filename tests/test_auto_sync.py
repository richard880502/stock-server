from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from quant_signal.application.data_sync import (
    DAILY_BARS_DATASET,
    TAIPEI,
    TWSE_SOURCE,
    DataSyncPending,
    ensure_symbol_bar_coverage,
)
from quant_signal.application.services import SignalService
from quant_signal.domain.models import Bar, FetchCoverage, JobStatus, SyncJobKind
from quant_signal.infrastructure.memory import MemoryQuantRepository

AS_OF = date(2026, 7, 31)


async def test_ensure_coverage_skips_when_already_sufficient(
    trending_bars: list[Bar],
) -> None:
    repository = MemoryQuantRepository()
    await repository.upsert_bars(trending_bars)

    performed = await ensure_symbol_bar_coverage(
        repository, "TEST", as_of=trending_bars[-1].trading_date
    )

    assert performed is False
    assert repository.sync_jobs == {}


async def test_ensure_coverage_enqueues_and_signals_pending_when_insufficient() -> None:
    repository = MemoryQuantRepository()

    with pytest.raises(DataSyncPending) as raised:
        await ensure_symbol_bar_coverage(repository, "2330.tw", as_of=AS_OF)

    assert raised.value.symbol == "2330.TW"
    assert raised.value.retry_after_seconds > 0
    (job,) = repository.sync_jobs.values()
    assert job.kind is SyncJobKind.SYMBOL
    assert job.symbol == "2330.TW"
    assert job.status is JobStatus.QUEUED


async def test_ensure_coverage_deduplicates_concurrent_requests() -> None:
    repository = MemoryQuantRepository()

    for _ in range(5):
        with pytest.raises(DataSyncPending):
            await ensure_symbol_bar_coverage(repository, "2330.TW", as_of=AS_OF)

    assert len(repository.sync_jobs) == 1


async def test_ensure_coverage_extends_lookback_for_a_past_as_of() -> None:
    repository = MemoryQuantRepository()
    past = date.today() - timedelta(days=400)

    with pytest.raises(DataSyncPending):
        await ensure_symbol_bar_coverage(repository, "2330.TW", as_of=past)

    (job,) = repository.sync_jobs.values()
    assert job.lookback_days >= 900


async def test_ensure_coverage_does_not_wait_forever_for_a_short_history() -> None:
    # A sync already ran this month and the symbol is still short (new listing):
    # the analysis must report insufficient history rather than queue again.
    repository = MemoryQuantRepository()
    now = datetime.now(UTC)
    await repository.record_fetch_coverage(
        [
            FetchCoverage(
                source=TWSE_SOURCE,
                dataset=DAILY_BARS_DATASET,
                symbol="9999.TW",
                period=now.astimezone(TAIPEI).strftime("%Y-%m"),
                status="ok",
                row_count=3,
                fetched_at=now,
            )
        ]
    )

    performed = await ensure_symbol_bar_coverage(repository, "9999.TW", as_of=AS_OF)

    assert performed is True
    assert repository.sync_jobs == {}


async def test_signal_service_default_never_enqueues() -> None:
    repository = MemoryQuantRepository()

    with pytest.raises(ValueError):
        await SignalService(repository).analyze("2330.TW", as_of=AS_OF)

    assert repository.sync_jobs == {}


async def test_signal_service_auto_sync_raises_pending_instead_of_fetching() -> None:
    repository = MemoryQuantRepository()

    with pytest.raises(DataSyncPending):
        await SignalService(repository, auto_sync=True).analyze("2330.TW", as_of=AS_OF)

    assert len(repository.sync_jobs) == 1
