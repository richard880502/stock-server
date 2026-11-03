"""Exercises the PostgreSQL repository against a real database.

Skipped unless ``QUANT_TEST_DATABASE_URL`` points at a migrated database, e.g.
``postgresql+asyncpg://quant:quant@127.0.0.1:15432/quant_signal`` from
``docker compose up -d postgres && uv run alembic upgrade head``.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time
from uuid import uuid4

import pytest
from fakes import FakeProvider
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from quant_signal.application.data_sync import TwseDataSyncService
from quant_signal.application.sync_jobs import enqueue_daily_refresh, process_next_job
from quant_signal.domain.models import FetchCoverage, JobStatus, SyncJobKind
from quant_signal.infrastructure.db import PostgresQuantRepository

DATABASE_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="QUANT_TEST_DATABASE_URL not set")


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(DATABASE_URL)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    async with factory() as session:
        await session.execute(text("DELETE FROM sync_jobs WHERE symbol LIKE 'ZZ%'"))
        await session.execute(text("DELETE FROM fetch_coverage WHERE symbol LIKE 'ZZ%'"))
        await session.execute(text("DELETE FROM fetch_coverage WHERE source = 'scheduler'"))
        await session.execute(text("DELETE FROM instruments WHERE symbol LIKE 'ZZ%'"))
        await session.commit()
    await engine.dispose()


def _symbol() -> str:
    return f"ZZ{uuid4().hex[:8].upper()}.TW"


async def test_concurrent_enqueues_collapse_into_one_active_job(sessions) -> None:
    symbol = _symbol()

    async def enqueue() -> bool:
        async with sessions() as session:
            _, created = await PostgresQuantRepository(session).enqueue_sync_job(
                SyncJobKind.SYMBOL, symbol
            )
            return created

    results = await asyncio.gather(*(enqueue() for _ in range(8)))

    assert sum(results) == 1


async def test_claim_is_exclusive_and_lifecycle_frees_the_slot(sessions) -> None:
    symbol = _symbol()
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        job, _ = await repo.enqueue_sync_job(SyncJobKind.SYMBOL, symbol, lookback_days=90)

    async def claim():
        async with sessions() as session:
            return await PostgresQuantRepository(session).claim_next_sync_job()

    claimed = [c for c in await asyncio.gather(*(claim() for _ in range(4))) if c]
    mine = [c for c in claimed if c.id == job.id]
    assert len(mine) == 1 and mine[0].attempts == 1 and mine[0].lookback_days == 90

    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        await repo.fail_sync_job(job.id, "boom", retry=True)
        again = await repo.claim_next_sync_job()
        assert again is not None and again.attempts == 2 and again.error == "boom"
        await repo.fail_sync_job(job.id, "boom", retry=False)
        fresh, created = await repo.enqueue_sync_job(SyncJobKind.SYMBOL, symbol)
        assert created and fresh.id != job.id and fresh.status is JobStatus.QUEUED


async def test_stale_running_jobs_are_requeued(sessions) -> None:
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        job, _ = await repo.enqueue_sync_job(SyncJobKind.SYMBOL, _symbol())
        # Only this test's job may be running, or the count would be ambiguous.
        claimed = await repo.claim_next_sync_job()
        assert claimed is not None
        assert await repo.requeue_stale_sync_jobs(older_than_seconds=3600) == 0
        assert await repo.requeue_stale_sync_jobs(older_than_seconds=0) >= 1


async def test_coverage_upsert_and_lookup(sessions) -> None:
    symbol = _symbol()
    entry = FetchCoverage(
        source="twse_official",
        dataset="daily_bars",
        symbol=symbol,
        period="2026-05",
        status="empty",
        fetched_at=datetime(2026, 6, 2, tzinfo=UTC),
    )
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        await repo.record_fetch_coverage([entry])
        await repo.record_fetch_coverage(
            [entry.model_copy(update={"status": "ok", "row_count": 20})]
        )
        found = await repo.list_fetch_coverage(
            "twse_official", "daily_bars", symbol, ["2026-05", "2026-06"]
        )
    assert list(found) == ["2026-05"]
    assert found["2026-05"].status == "ok" and found["2026-05"].row_count == 20


async def test_incremental_sync_and_worker_against_postgres(sessions) -> None:
    symbol = _symbol()
    provider = FakeProvider()
    clock_now = datetime.combine(date(2026, 7, 15), time(16), tzinfo=UTC)

    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        service = TwseDataSyncService(repo, provider, clock=lambda: clock_now)
        first = await service.sync_symbol(symbol, start=date(2026, 4, 1), end=date(2026, 7, 15))
        assert first.bars_inserted > 40

    later = datetime.combine(date(2026, 7, 16), time(16), tzinfo=UTC)
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        service = TwseDataSyncService(repo, provider, clock=lambda: later)
        second = await service.sync_symbol(symbol, start=date(2026, 4, 1), end=date(2026, 7, 16))
        assert second.periods_skipped == 3 and second.periods_fetched == 1
        assert symbol in await repo.list_tracked_symbols()

    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        await repo.enqueue_sync_job(SyncJobKind.SYMBOL, symbol, lookback_days=60)
        outcome = await process_next_job(
            repo, provider, provider, max_attempts=3, today=date(2026, 7, 16)  # type: ignore[arg-type]
        )
        assert outcome is True


class _OnlyTestSymbols(PostgresQuantRepository):
    """Keeps the scheduler away from real instruments in a shared dev database."""

    async def list_tracked_symbols(self) -> list[str]:
        return [s for s in await super().list_tracked_symbols() if s.startswith("ZZ")]


async def test_daily_refresh_is_recorded_so_it_runs_once(sessions) -> None:
    symbol = _symbol()
    now = datetime(2099, 1, 5, 8, 0, tzinfo=UTC)  # a Monday, after the cutoff
    async with sessions() as session:
        repo = _OnlyTestSymbols(session)
        await repo.upsert_bars(
            await FakeProvider().fetch_symbol_bars(
                symbol, start=date(2026, 7, 1), end=date(2026, 7, 3)
            ),
            market="TW",
        )
        first = await enqueue_daily_refresh(
            repo, now=now, daily_time="15:30", lookback_days=500
        )
        second = await enqueue_daily_refresh(
            repo, now=now, daily_time="15:30", lookback_days=500
        )
    assert first == 2  # prices + institutional flows for the one tracked symbol
    assert second == 0
