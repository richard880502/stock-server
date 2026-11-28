"""Migration 0022 against a real PostgreSQL (skipped without ``QUANT_TEST_DATABASE_URL``)."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import AsyncIterator
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="QUANT_TEST_DATABASE_URL not set")

MIGRATION = (
    Path(__file__).parents[1] / "migrations" / "versions" / "0022_restamp_late_fetched_rows.py"
)
TAIPEI = ZoneInfo("Asia/Taipei")
TABLES = ("valuation_daily", "short_lending_daily", "day_trade_daily", "foreign_holding_daily")


def at(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=TAIPEI)


def load_migration():
    spec = importlib.util.spec_from_file_location("migration_0022", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_is_chained_after_0021_with_a_documented_noop_downgrade() -> None:
    module = load_migration()
    assert module.down_revision == "0021_short_daytrade_events"
    assert module.revision == "0022_restamp_late_fetched_rows"
    assert module.downgrade() is None and "no-op" in module.__doc__


@pytest.fixture
async def engine() -> AsyncIterator:
    engine = create_async_engine(DATABASE_URL)  # type: ignore[arg-type]
    yield engine
    async with engine.begin() as conn:
        for table in TABLES:
            await conn.execute(text(f"DELETE FROM {table} WHERE symbol LIKE 'ZZ%'"))
    await engine.dispose()


async def test_late_fetched_rows_are_restamped_and_on_time_rows_are_untouched(engine) -> None:
    tag = uuid4().hex[:6].upper()
    sym = f"ZZ{tag}.TW"
    saturday = at(11, 28, 10, 55)
    async with engine.begin() as conn:
        for day, stamp in (
            (date(2026, 11, 27), saturday),  # late: should be 18:00 of the 27th
            (date(2026, 11, 26), at(11, 26, 18)),  # exactly on time
            (date(2026, 11, 25), at(11, 25, 15, 30)),  # fetched earlier than publication
        ):
            await conn.execute(
                text(
                    "INSERT INTO valuation_daily (symbol, trading_date, source, pe_ratio, "
                    "available_at) VALUES (:s, :d, 'twse', 10, :a)"
                ),
                {"s": sym, "d": day, "a": stamp},
            )
        await conn.execute(
            text(
                "INSERT INTO foreign_holding_daily (symbol, trading_date, source, "
                "shares_outstanding, foreign_shares, foreign_holding_pct, available_at) "
                "VALUES (:s, :d, 'twse', 100, 10, 10.0, :a), (:s, :d2, 'twse', 100, 10, 10.0, :a2)"
            ),
            {
                "s": sym,
                "d": date(2026, 11, 27),
                "a": saturday,
                "d2": date(2026, 11, 26),
                "a2": at(11, 26, 20),
            },
        )
        for revision, stamp in ((1, saturday), (2, at(11, 29, 9))):
            await conn.execute(
                text(
                    "INSERT INTO short_lending_daily (symbol, trading_date, revision, source, "
                    "sbl_balance, available_at) VALUES (:s, :d, :r, 'twse_twt93u', 5, :a)"
                ),
                {"s": sym, "d": date(2026, 11, 27), "r": revision, "a": stamp},
            )
        # 當沖 final on T+2 weekdays: Wed 11/18 -> Fri 11/20 23:00, Thu 11/19 -> Mon 11/23 23:00;
        # Fri 11/27 would be Tue 12/01, later than the Saturday fetch -> keeps the fetch time
        for day in (date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 27)):
            await conn.execute(
                text(
                    "INSERT INTO day_trade_daily (symbol, trading_date, revision, source, "
                    "day_trade_volume, available_at) VALUES (:s, :d, 1, 'twse_twtb4u', 1, :a)"
                ),
                {"s": sym, "d": day, "a": saturday},
            )

    module = load_migration()
    async with engine.begin() as conn:
        first = await conn.run_sync(module.restamp)
    assert first["valuation_daily"] >= 1 and first["day_trade_daily"] >= 2

    async def stamps(table: str, *keys: str) -> list:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    f"SELECT {', '.join(keys)}, available_at FROM {table} "  # noqa: S608
                    "WHERE symbol = :s ORDER BY 1, 2"
                ),
                {"s": sym},
            )
            return [tuple(r) for r in rows]

    assert await stamps("valuation_daily", "trading_date") == [
        (date(2026, 11, 25), at(11, 25, 15, 30)),
        (date(2026, 11, 26), at(11, 26, 18)),
        (date(2026, 11, 27), at(11, 27, 18)),
    ]
    assert await stamps("foreign_holding_daily", "trading_date") == [
        (date(2026, 11, 26), at(11, 26, 20)),
        (date(2026, 11, 27), at(11, 27, 21)),
    ]
    assert await stamps("short_lending_daily", "trading_date", "revision") == [
        (date(2026, 11, 27), 1, at(11, 27, 22, 35)),
        (date(2026, 11, 27), 2, at(11, 29, 9)),  # a genuine later revision stays
    ]
    assert await stamps("day_trade_daily", "trading_date") == [
        (date(2026, 11, 18), at(11, 20, 23)),
        (date(2026, 11, 19), at(11, 23, 23)),
        (date(2026, 11, 27), saturday),
    ]
    # idempotent: a second run changes nothing
    async with engine.begin() as conn:
        second = await conn.run_sync(module.restamp)
    assert not any(second.values())


async def test_feed_day_stats_query_runs_for_every_feed(engine) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from quant_signal.domain.feed_stats import FEEDS
    from quant_signal.infrastructure.db import PostgresQuantRepository

    sym = f"ZZ{uuid4().hex[:6].upper()}.TW"
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO valuation_daily (symbol, trading_date, source, pe_ratio, "
                "available_at) VALUES (:s, :d, 'twse', 10, :a), (:s, :d, 'tpex', 11, :b)"
            ),
            {"s": sym, "d": date(2026, 11, 27), "a": at(11, 27, 18), "b": at(11, 28, 10)},
        )
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        stats = await PostgresQuantRepository(session).feed_day_stats(
            date(2026, 11, 27), date(2026, 11, 27)
        )
    assert set(stats) == set(FEEDS)
    mine = [s for s in stats["valuation"] if s.rows >= 2]
    assert mine and mine[0].first_available_at == at(11, 27, 18)
    assert mine[0].last_available_at >= at(11, 28, 10)
