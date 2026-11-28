"""ETF storage, reads and search against a real PostgreSQL (``etf_daily``).

Skipped unless ``QUANT_TEST_DATABASE_URL`` points at a migrated, dedicated database.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from quant_signal.application.market_sync import MarketSnapshotSyncService
from quant_signal.application.paper_market import load_market, official_closes_on
from quant_signal.domain.etf import EtfDay
from quant_signal.domain.models import MarketStockDay
from quant_signal.infrastructure.db import PostgresQuantRepository

DATABASE_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="QUANT_TEST_DATABASE_URL not set")

# Dates far from real data so the shared "newest day" reads are ours.
BASE = date(2031, 3, 3)


def _code(suffix: str) -> str:
    """A well-formed ETF code unlikely to exist (the database is dedicated to the tests)."""
    return f"00{uuid4().int % 9000 + 1000}{suffix}"


def _row(symbol: str, day: date, close: float, name: str = "測試ETF", **kw) -> EtfDay:
    return EtfDay(
        symbol=symbol,
        name=name,
        market="TWO" if symbol.endswith(".TWO") else "TW",
        trading_date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        change=kw.pop("change", 0.1),
        volume=1_000_000,
        turnover_value=close * 1_000_000,
        trades=10,
        available_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=10),
        source="twse_official",
        **kw,
    )


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(DATABASE_URL)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    async with factory() as session:
        await session.execute(text("DELETE FROM etf_daily WHERE trading_date >= '2031-01-01'"))
        await session.execute(text("DELETE FROM etf_daily WHERE symbol = '009998.TW'"))
        await session.execute(text("DELETE FROM fetch_coverage WHERE source = 'etf_test'"))
        await session.commit()
    await engine.dispose()


async def test_upsert_is_idempotent_and_updates_by_primary_key(sessions) -> None:
    symbol = _code(".TW")
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        assert await repo.upsert_etf_days([_row(symbol, BASE, 10.0)]) == 1
        assert await repo.upsert_etf_days([_row(symbol, BASE, 11.5, name="改名")]) == 1
        rows = await repo.list_etf_rows([symbol])
    assert len(rows) == 1 and rows[0].close == 11.5 and rows[0].name == "改名"
    assert rows[0].volume == 1_000_000 and rows[0].trades == 10


async def test_list_rows_returns_the_newest_sessions_per_symbol(sessions) -> None:
    a, b = _code(".TW"), _code(".TWO")
    days = [BASE + timedelta(days=i) for i in range(5)]
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        await repo.upsert_etf_days([_row(a, d, 10.0 + i) for i, d in enumerate(days)])
        await repo.upsert_etf_days([_row(b, d, 20.0) for d in days[:2]])
        newest = await repo.list_etf_rows([a, b], sessions=3)
        assert [r.trading_date for r in newest if r.symbol == a] == days[2:]
        assert [r.trading_date for r in newest if r.symbol == b] == days[:2]
        as_of = await repo.list_etf_rows([a], as_of=days[1], sessions=3)
        assert [r.trading_date for r in as_of] == days[:2]
        assert await repo.list_etf_rows([], sessions=3) == []
        # paper reads: market view and the official close of exactly one day
        views = await load_market(repo, [a, b, "009999.TW"])
        assert views[a].known and views[a].official_close == Decimal("14.00")
        assert views[a].official_prev == Decimal("13.90")
        assert views[b].market == "TWO" and not views["009999.TW"].known
        assert await official_closes_on(repo, [a, b], days[1]) == {
            a: Decimal("11.00"),
            b: Decimal("20.00"),
        }
        assert await official_closes_on(repo, [b], days[4]) == {}


async def test_search_matches_code_or_name_and_flags_etf(sessions) -> None:
    code = _code(".TW")
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        await repo.upsert_etf_days(
            [
                _row(code, BASE, 30.0, name="舊名"),
                _row(code, BASE + timedelta(days=1), 31.0, name="新名高息"),
            ]
        )
        by_code = await repo.search_etf_instruments(code.split(".")[0])
        by_name = await repo.search_etf_instruments("新名高息")
        assert [(i.symbol, i.name, i.kind) for i in by_code] == [(code, "新名高息", "etf")]
        assert [i.symbol for i in by_name] == [code]
        assert await repo.search_etf_instruments("   ") == []


async def test_column_limits_fail_like_postgres(sessions) -> None:
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        with pytest.raises(DBAPIError):
            await repo.upsert_etf_days([_row("00" + "9" * 40 + ".TW", BASE, 10.0)])
        await session.rollback()
    from quant_signal.infrastructure.memory import MemoryQuantRepository

    with pytest.raises(ValueError):
        await MemoryQuantRepository().upsert_etf_days([_row("00" + "9" * 40 + ".TW", BASE, 10.0)])


class _Provider:
    async def fetch_market_stock_day(self, day: date):
        return await self._stocks(day)

    async def fetch_market_day_rows(self, day: date):
        return await self._stocks(day), [_row("009998.TW", day, 12.0)]

    async def fetch_market_etf_day(self, day: date):
        return [_row("009998.TW", day, 12.0)]

    async def _stocks(self, day: date):
        return [
            MarketStockDay(
                symbol="ZZ9999.TW",
                name="測試",
                market="TW",
                trading_date=day,
                open=10,
                high=10,
                low=10,
                close=10,
                volume=1,
                turnover_value=10,
                available_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC),
                source="etf_test",
            )
        ]


async def test_sync_stores_etf_rows_and_coverage_in_postgres(sessions) -> None:
    day = date(2026, 11, 27)
    now = datetime(2026, 11, 28, 8, 0, tzinfo=UTC)
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        service = MarketSnapshotSyncService(
            repo, _Provider(), market="TW", source="etf_test", clock=lambda: now
        )
        await service.sync(start=day, end=day)
        stored = await repo.list_etf_rows(["009998.TW"], sessions=1)
        coverage = await repo.list_fetch_coverage("etf_test", "etf_day", "TW", [day.isoformat()])
        stock_cov = await repo.list_fetch_coverage(
            "etf_test", "market_stock_day", "TW", [day.isoformat()]
        )
        assert stored[0].available_at == now.replace(hour=10, day=27)  # 18:00 Taipei publication
        assert (
            coverage[day.isoformat()].status == "ok" and stock_cov[day.isoformat()].row_count == 1
        )
        await session.execute(text("DELETE FROM etf_daily WHERE symbol = '009998.TW'"))
        await session.execute(text("DELETE FROM market_stock_daily WHERE symbol = 'ZZ9999.TW'"))
        await session.commit()
