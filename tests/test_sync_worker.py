from __future__ import annotations

from datetime import UTC, date, datetime

from fakes import FakeProvider

from quant_signal.application.sync_jobs import (
    enqueue_daily_refresh,
    process_next_job,
    retry_delay_seconds,
)
from quant_signal.domain.models import Instrument, JobStatus, SyncJobKind
from quant_signal.infrastructure.memory import MemoryQuantRepository

# 2026-07-15 is a Wednesday. 08:00 UTC = 16:00 Taipei (after the 15:30 cutoff).
AFTER_CLOSE = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
BEFORE_CLOSE = datetime(2026, 7, 15, 3, 0, tzinfo=UTC)  # 11:00 Taipei
SATURDAY = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


async def _tracked(repository: MemoryQuantRepository, *symbols: str) -> None:
    for symbol in symbols:
        market = "TWO" if symbol.endswith(".TWO") else "TW"
        repository.instruments[symbol] = Instrument(symbol=symbol, market=market)


async def test_daily_refresh_queues_prices_and_flows_once_per_day() -> None:
    repository = MemoryQuantRepository()
    await _tracked(repository, "2330.TW", "^TWII", "6488.TWO")

    created = await enqueue_daily_refresh(
        repository, now=AFTER_CLOSE, daily_time="15:30", lookback_days=500
    )
    again = await enqueue_daily_refresh(
        repository, now=AFTER_CLOSE, daily_time="15:30", lookback_days=500
    )

    assert created == 5  # 3 price jobs + 2 flow jobs (the index has no flows)
    assert again == 0
    kinds = sorted((job.kind.value, job.symbol) for job in repository.sync_jobs.values())
    assert ("institutions", "^TWII") not in kinds
    assert ("symbol", "^TWII") in kinds


async def test_daily_refresh_waits_for_close_and_skips_weekends() -> None:
    repository = MemoryQuantRepository()
    await _tracked(repository, "2330.TW")

    assert await enqueue_daily_refresh(
        repository, now=BEFORE_CLOSE, daily_time="15:30", lookback_days=500
    ) == 0
    assert await enqueue_daily_refresh(
        repository, now=SATURDAY, daily_time="15:30", lookback_days=500
    ) == 0
    assert repository.sync_jobs == {}


async def test_worker_runs_a_queued_job_and_marks_it_completed() -> None:
    repository = MemoryQuantRepository()
    provider = FakeProvider()
    job, created = await repository.enqueue_sync_job(
        SyncJobKind.SYMBOL, "2330.TW", lookback_days=90
    )
    assert created

    outcome = await process_next_job(
        repository, provider, provider, max_attempts=3, today=date(2026, 7, 15)  # type: ignore[arg-type]
    )

    assert outcome is True
    assert repository.sync_jobs[job.id].status is JobStatus.COMPLETED
    assert len(await repository.list_bars("2330.TW")) > 40
    # Nothing left to do.
    assert await process_next_job(
        repository, provider, provider, max_attempts=3, today=date(2026, 7, 15)  # type: ignore[arg-type]
    ) is None


async def test_worker_retries_then_gives_up_after_max_attempts() -> None:
    class Failing(FakeProvider):
        async def fetch_symbol_bars(self, symbol, *, start, end):  # type: ignore[override]
            raise RuntimeError("upstream down")

    repository = MemoryQuantRepository()
    provider = Failing()
    job, _ = await repository.enqueue_sync_job(SyncJobKind.SYMBOL, "2330.TW")

    results = [
        await process_next_job(
            repository, provider, provider, max_attempts=2, today=date(2026, 7, 15)  # type: ignore[arg-type]
        )
        for _ in range(2)
    ]

    assert results == [False, False]
    final = repository.sync_jobs[job.id]
    assert final.status is JobStatus.FAILED
    assert final.attempts == 2
    assert final.error == "upstream down"


async def test_a_failed_job_frees_the_slot_for_a_new_request() -> None:
    repository = MemoryQuantRepository()
    job, _ = await repository.enqueue_sync_job(SyncJobKind.SYMBOL, "2330.TW")
    claimed = await repository.claim_next_sync_job()
    assert claimed is not None
    await repository.fail_sync_job(job.id, "boom", retry=False)

    new_job, created = await repository.enqueue_sync_job(SyncJobKind.SYMBOL, "2330.TW")

    assert created and new_job.id != job.id


async def test_stale_running_jobs_are_requeued() -> None:
    repository = MemoryQuantRepository()
    await repository.enqueue_sync_job(SyncJobKind.SYMBOL, "2330.TW")
    await repository.claim_next_sync_job()

    assert await repository.requeue_stale_sync_jobs(older_than_seconds=3600) == 0
    assert await repository.requeue_stale_sync_jobs(older_than_seconds=0) == 1
    assert (await repository.claim_next_sync_job()) is not None


def test_retry_delay_backs_off_and_is_capped() -> None:
    assert retry_delay_seconds(5, 1) == 10
    assert retry_delay_seconds(5, 3) == 40
    assert retry_delay_seconds(5, 20) == 120
