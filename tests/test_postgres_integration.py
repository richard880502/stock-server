"""Exercises the PostgreSQL repository against a real database.

Skipped unless ``QUANT_TEST_DATABASE_URL`` points at a migrated, **dedicated**
database. Several tests claim whichever job is oldest and the daily-refresh test
queues jobs for every tracked symbol, so never aim this at a database a live
worker is using::

    docker compose up -d postgres
    docker compose exec postgres psql -U quant -d postgres -c "create database quant_test"
    URL=postgresql+asyncpg://quant:quant@127.0.0.1:15432/quant_test
    DATABASE_URL=$URL uv run alembic upgrade head
    QUANT_TEST_DATABASE_URL=$URL uv run --all-groups pytest tests/test_postgres_integration.py
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

import pytest
from fakes import FakeProvider
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from quant_signal.application.data_sync import TwseDataSyncService
from quant_signal.application.market_rankings import MarketRankingService
from quant_signal.application.sync_jobs import enqueue_daily_refresh, process_next_job
from quant_signal.domain.models import FetchCoverage, JobStatus, MarketStockDay, SyncJobKind
from quant_signal.infrastructure.db import PostgresQuantRepository

DATABASE_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="QUANT_TEST_DATABASE_URL not set")


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(DATABASE_URL)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    async with factory() as session:
        await session.execute(text("DELETE FROM sync_jobs"))
        await session.execute(text("DELETE FROM fetch_coverage WHERE symbol LIKE 'ZZ%'"))
        await session.execute(text("DELETE FROM fetch_coverage WHERE source = 'scheduler'"))
        await session.execute(text("DELETE FROM instruments WHERE symbol LIKE 'ZZ%'"))
        await session.execute(text("DELETE FROM market_stock_daily WHERE symbol LIKE 'ZZ%'"))
        await session.execute(text("DELETE FROM users WHERE google_sub LIKE 'zz-%'"))
        await session.execute(text("DELETE FROM market_industries WHERE symbol LIKE 'ZZ%'"))
        await session.execute(text("DELETE FROM market_factor_daily WHERE symbol LIKE 'ZZ%'"))
        await session.execute(
            text("DELETE FROM quote_latest WHERE symbol LIKE 'ZZ%'")
        )
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
    assert first == 4  # 2 market jobs + prices and flows for the one tracked symbol
    assert second == 0


# Far-future dates keep these rows isolated from any real data in a dev database.
Z1, Z2, Z3 = date(2099, 3, 2), date(2099, 3, 3), date(2099, 3, 4)


def _market_row(symbol: str, day: date, **kwargs) -> MarketStockDay:
    base = {
        "name": symbol,
        "market": "TW",
        "open": 100.0,
        "high": 100.0,
        "low": 100.0,
        "close": 100.0,
        "volume": 2_000_000,
        "turnover_value": 2e8,
        "change": 1.0,
        "foreign_net": 0,
        "trust_net": 0,
        "dealer_net": 0,
        "total_net": 0,
        "margin_balance": 500,
        "margin_change": 10,
        "short_balance": 5,
        "short_change": 1,
        "available_at": datetime.combine(day, time(10), tzinfo=UTC),
        "source": "twse_official",
    }
    return MarketStockDay(symbol=symbol, trading_date=day, **{**base, **kwargs})


async def test_market_stock_days_rank_and_aggregate_in_sql(sessions) -> None:
    a, b = _symbol(), _symbol()
    rows = [
        _market_row(a, Z1, foreign_net=1_000_000, close=100.0),
        _market_row(a, Z2, foreign_net=2_000_000, close=110.0, change=10.0),
        _market_row(a, Z3, foreign_net=3_000_000, close=120.0, change=10.0),
        _market_row(b, Z1, foreign_net=-500_000, close=50.0, change=-1.0),
        _market_row(b, Z2, foreign_net=-500_000, close=50.0, change=0.0),
        _market_row(b, Z3, foreign_net=None, close=50.0, change=0.0, margin_balance=None),
    ]
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        assert await repo.upsert_market_stock_days(rows) == 6
        # Re-running is idempotent and updates in place.
        assert await repo.upsert_market_stock_days(rows[:1]) == 1
        service = MarketRankingService(repo)

        ranking = await service.ranking("foreign_net", window=3, as_of=Z3)
        assert ranking.trading_days == 3 and ranking.data_through == Z3
        assert [(e.symbol, e.value) for e in ranking.entries] == [(a, 6000.0), (b, -1000.0)]

        sellers = await service.ranking("foreign_net", window=3, as_of=Z3, order="asc")
        assert sellers.entries[0].symbol == b

        flows = await service.flows(days=3, as_of=Z3)
        first, last = flows.days[0], flows.days[-1]
        assert first.trading_date == Z1
        assert first.foreign_net_value == 1_000_000 * 100 - 500_000 * 50
        assert first.foreign_net_shares == 500_000
        assert first.advancing == 1 and first.declining == 1
        assert last.margin_balance == 500  # the NULL balance counts as 0
        assert last.unchanged == 1

        # Point in time: nothing published after the cutoff is visible.
        early = await service.ranking("foreign_net", window=3, as_of=Z2)
        assert early.data_through == Z2 and early.trading_days == 2

        status = {s.market: s for s in await service.status()}
        assert status["TW"].last_date >= Z3


async def test_active_market_job_is_reported(sessions) -> None:
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        job, created = await repo.enqueue_sync_job(SyncJobKind.MARKET, "ZZTW", lookback_days=30)
        assert created and await repo.has_active_sync_job(SyncJobKind.MARKET)
        # Close it directly: claiming would grab whichever job is oldest.
        await repo.fail_sync_job(job.id, "x", retry=False)


async def test_users_sessions_and_atomic_usage_counters(sessions) -> None:
    from datetime import timedelta

    sub = f"zz-{uuid4().hex}"
    now = datetime.now(UTC)
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        first = await repo.upsert_google_user(
            google_sub=sub, email="a@x.com", name="A", picture=None
        )
        again = await repo.upsert_google_user(
            google_sub=sub, email="a2@x.com", name="A2", picture="p"
        )
        assert again.id == first.id and again.email == "a2@x.com"

        await repo.create_user_session(
            user_id=first.id, token_hash="h" * 64, expires_at=now + timedelta(days=1)
        )
        assert (await repo.get_user_by_session("h" * 64, now=now)).id == first.id
        assert await repo.get_user_by_session("h" * 64, now=now + timedelta(days=2)) is None
        await repo.delete_user_session("h" * 64)
        assert await repo.get_user_by_session("h" * 64, now=now) is None

    day = date(2099, 3, 4)

    async def use() -> bool:
        async with sessions() as session:
            allowed, _ = await PostgresQuantRepository(session).consume_usage(
                first.id, "llm", day, limit=3
            )
            return allowed

    results = await asyncio.gather(*(use() for _ in range(10)))
    assert sum(results) == 3  # exactly the limit is granted under concurrency

    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        assert await repo.consume_usage(first.id, "llm", day, limit=3) == (False, 3)
        assert await repo.consume_usage(first.id, "forecast", day, limit=1) == (True, 1)
        assert await repo.consume_usage(first.id, "forecast", day, limit=0) == (False, 0)


async def test_active_sync_jobs_are_listed_by_kind(sessions) -> None:
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        market, _ = await repo.enqueue_sync_job(SyncJobKind.MARKET, "ZZTW", lookback_days=30)
        await repo.enqueue_sync_job(SyncJobKind.SYMBOL, _symbol(), lookback_days=30)

        active = await repo.list_active_sync_jobs(SyncJobKind.MARKET)
        await repo.fail_sync_job(market.id, "x", retry=False)
        after = await repo.list_active_sync_jobs(SyncJobKind.MARKET)

    assert [job.symbol for job in active] == ["ZZTW"]
    assert after == []


async def test_market_search_and_watchlist_cap_in_sql(sessions) -> None:
    day = date(2099, 3, 5)  # later than any other fixture date: search reads the newest session
    needle = uuid4().hex[:8].upper()
    names = {f"ZZ{needle}{i}.TW": f"測試{needle}{i}" for i in range(3)}
    await _seed_market(sessions, names, day)

    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        by_code = await repo.search_market_stocks(f"zz{needle}1")
        by_name = await repo.search_market_stocks(f"測試{needle}")
        assert [i.symbol for i in by_code] == [f"ZZ{needle}1.TW"] and by_code[0].tracked is False
        assert {i.symbol for i in by_name} == set(names)

        rows = await repo.list_market_stock_rows([f"zz{needle}0.tw"], [day])
        assert [r.symbol for r in rows] == [f"ZZ{needle}0.TW"]

        user = await repo.upsert_google_user(
            google_sub=f"zz-{uuid4().hex}", email="w@x.com", name=None, picture=None
        )
        symbols = list(names)
        assert await _add(repo, user.id, symbols[0], limit=2) == "added"
        assert await _add(repo, user.id, symbols[0], limit=2) == "exists"
        assert await _add(repo, user.id, symbols[1], limit=2) == "added"
        assert await _add(repo, user.id, symbols[2], limit=2) == "full"
        listed = await repo.list_watchlist(user.id)
        assert [row[0] for row in listed] == symbols[:2]
        assert await repo.remove_watchlist_item(user.id, symbols[0]) is True
        assert await repo.remove_watchlist_item(user.id, symbols[0]) is False
        assert await _add(repo, user.id, symbols[2], limit=2) == "added"


async def test_concurrent_watchlist_adds_never_exceed_the_limit(sessions) -> None:
    async with sessions() as session:
        user = await PostgresQuantRepository(session).upsert_google_user(
            google_sub=f"zz-{uuid4().hex}", email="c@x.com", name=None, picture=None
        )
    symbols = [_symbol() for _ in range(10)]

    async def add(symbol: str) -> str:
        async with sessions() as session:
            return await _add(PostgresQuantRepository(session), user.id, symbol, limit=3)

    outcomes = await asyncio.gather(*(add(symbol) for symbol in symbols))

    assert outcomes.count("added") == 3 and outcomes.count("full") == 7
    async with sessions() as session:
        assert len(await PostgresQuantRepository(session).list_watchlist(user.id)) == 3


async def _add(repo, user_id, symbol: str, *, limit: int) -> str:
    return await repo.add_watchlist_item(
        user_id, symbol=symbol, name=symbol, market="TW", limit=limit
    )


async def _seed_market(sessions, names: dict[str, str], day: date) -> None:
    async with sessions() as session:
        await PostgresQuantRepository(session).upsert_market_stock_days(
            [_market_row(symbol, day, name=name) for symbol, name in names.items()]
        )


async def test_industries_and_intraday_snapshots_roundtrip(sessions) -> None:
    from quant_signal.domain.models import MarketIndustry
    from quant_signal.infrastructure.db.prices import LatestQuote, store_latest

    symbol = _symbol()
    slot = datetime(2026, 11, 2, 2, 30, tzinfo=UTC)  # 10:30 Taipei on a Monday

    def quote(at: datetime, price: float, stored: datetime) -> LatestQuote:
        return LatestQuote(
            symbol=symbol,
            price=price,
            reference=100.0,
            open=None,
            high=None,
            low=None,
            volume=5,
            amount=1_000_000,
            trade_time=at,
            source="snapshot",
            updated_at=stored,
        )

    async with sessions() as session:
        repository = PostgresQuantRepository(session)
        industry = MarketIndustry(
            symbol=symbol,
            market="TW",
            industry_code="24",
            industry_name="半導體業",
            source="twse_openapi",
            available_at=slot,
        )
        await repository.upsert_market_industries([industry])
        await repository.upsert_market_industries([industry])
        stored = [i for i in await repository.list_market_industries() if i.symbol == symbol]
        assert len(stored) == 1 and stored[0].industry_name == "半導體業"

        await store_latest(session, [quote(slot + timedelta(minutes=10), 101, slot)])
        # An older snapshot never replaces a newer price.
        await store_latest(session, [quote(slot, 99, slot)])
        await session.commit()
        now = slot + timedelta(minutes=15)
        latest = [
            i
            for i in await repository.latest_intraday_snapshots(day=date(2026, 11, 2), now=now)
            if i.symbol == symbol
        ]
        assert [(i.taken_at, i.price, round(i.change_pct, 4)) for i in latest] == [
            (slot + timedelta(minutes=10), 101, 0.01)
        ]
        # Not yet stored at an earlier "now" (point in time).
        assert not [
            i
            for i in await repository.latest_intraday_snapshots(
                day=date(2026, 11, 2), now=slot - timedelta(minutes=1)
            )
            if i.symbol == symbol
        ]


# --- news pipeline against real SQL ----------------------------------------------------


async def test_news_upsert_and_point_in_time_reads_on_postgres(sessions) -> None:
    from quant_signal.domain.models import NewsItem, taipei_day_end  # noqa: F401

    symbol = _symbol()
    published = datetime(2026, 11, 20, 2, tzinfo=UTC)
    fetched = datetime(2026, 11, 26, 2, tzinfo=UTC)
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        # never auto-creates an instrument
        orphan = NewsItem(
            symbol=symbol, headline="孤兒", url="https://a.test/o", source="g",
            published_at=published, retrieved_at=fetched,
        )
        assert await repo.upsert_news_items([orphan]) == 0
        assert not await repo.instrument_exists(symbol)

        await session.execute(
            text("INSERT INTO instruments (id, symbol, name, market) VALUES (:i, :s, 'ZZ', 'TW')"),
            {"i": uuid4(), "s": symbol},
        )
        await session.commit()
        assert await repo.instrument_exists(symbol)

        unlabeled = NewsItem(
            symbol=symbol, headline="標題", url="https://a.test/1", source="g",
            published_at=published, retrieved_at=fetched, available_at=fetched,
        )
        assert await repo.upsert_news_items([unlabeled]) == 1

        # re-upsert with a label: classified_at is stored, first-seen times stay
        later = fetched + timedelta(days=2)  # 11/28 02:00 UTC = 11/28 10:00 Taipei
        labeled = unlabeled.model_copy(
            update={
                "sentiment_label": "bullish", "sentiment_score": 0.6, "classified_at": later,
                "retrieved_at": later, "available_at": later,
            }
        )
        await repo.upsert_news_items([labeled])
        row = (await repo.list_news_items(symbol))[0]
        assert row.sentiment_label == "bullish" and row.classified_at == later
        assert row.available_at == fetched and row.retrieved_at == fetched

        # re-upsert without a label keeps the existing label
        await repo.upsert_news_items([unlabeled.model_copy(update={"retrieved_at": later})])
        row = (await repo.list_news_items(symbol))[0]
        assert row.sentiment_label == "bullish" and row.sentiment_score == 0.6
        assert row.classified_at == later and row.available_at == fetched

        # as-of reads: published 11/20 but fetched 11/26 is invisible on 11/21 and 11/25
        assert await repo.list_news_items(symbol, end=date(2026, 11, 21)) == []
        assert await repo.list_news_items(symbol, end=date(2026, 11, 25)) == []
        # visible once fetched, but the label (written 11/28) shows only from 11/28 on
        for as_of in (date(2026, 11, 26), date(2026, 11, 27)):
            seen = (await repo.list_news_items(symbol, end=as_of))[0]
            assert seen.sentiment_label is None and seen.sentiment_score is None
        assert (await repo.list_news_items(symbol, end=date(2026, 11, 28)))[0].sentiment_label == (
            "bullish"
        )
        assert len(await repo.list_news_items(symbol, start=date(2026, 11, 19))) == 1
        assert await repo.list_news_items(symbol, start=date(2026, 11, 21)) == []


async def test_daily_bars_still_read_after_the_news_columns(sessions) -> None:
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        assert await repo.list_bars("ZZNOBARS.TW") == []  # selects every bars_daily column


async def test_every_orm_column_exists_in_the_migrated_database() -> None:
    from sqlalchemy import inspect

    from quant_signal.infrastructure.db.models import Base

    def drift(connection) -> list[str]:
        inspector = inspect(connection)
        db_tables = set(inspector.get_table_names())
        missing: list[str] = []
        for table in Base.metadata.sorted_tables:
            if table.name not in db_tables:
                missing.append(f"{table.name} (whole table)")
                continue
            db_columns = {column["name"] for column in inspector.get_columns(table.name)}
            missing += [
                f"{table.name}.{column.name}"
                for column in table.columns
                if column.name not in db_columns
            ]
        return missing

    engine = create_async_engine(DATABASE_URL)  # type: ignore[arg-type]
    try:
        async with engine.connect() as connection:
            missing = await connection.run_sync(drift)
    finally:
        await engine.dispose()
    assert missing == [], f"ORM columns missing from the database: {missing}"


async def test_factor_snapshot_storage_and_history_stream(sessions) -> None:
    from quant_signal.domain.models import MarketFactorSnapshot

    a, b = _symbol(), _symbol()
    days = [Z1 + timedelta(days=i) for i in range(5)]
    rows = [_market_row(sym, day, close=100.0 + i) for sym in (a, b) for i, day in enumerate(days)]
    rows.append(_market_row(b, days[0] - timedelta(days=30)))  # outside the 3-session window
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        await repo.upsert_market_stock_days(rows)

        end = days[-1]
        streamed = {}
        async for symbol, name, market, history in repo.iter_market_histories(end, 3):
            if symbol in (a, b):
                streamed[symbol] = (name, market, history)
        assert set(streamed) == {a, b}
        name, market, history = streamed[a]
        assert market == "TW" and [row[0] for row in history] == days[-3:]
        assert history[-1][1] == 104.0 and len(history[-1]) == 15
        assert (await repo.market_flow_through(end))["TW"] >= end

        def snapshot(symbol: str, day: date) -> MarketFactorSnapshot:
            return MarketFactorSnapshot(
                symbol=symbol,
                trading_date=day,
                version="zz_test",
                name=symbol,
                market="TW",
                close=100.0,
                composite=55.5,
                scores={"flow": 70.0, "trend": None},
                metrics={"ret_20d": 1.5, "trend_aligned": True, "revenue_period": None},
                highlights=["投信連買 3 日"],
                flags=[],
                available_at=datetime.combine(day, time(12), tzinfo=UTC),
            )

        for day in days[:3]:
            assert await repo.replace_market_factors(day, "zz_test", [snapshot(a, day)]) == 1
        # replacing a date is idempotent
        assert await repo.replace_market_factors(days[2], "zz_test", [snapshot(a, days[2])]) == 1
        loaded = await repo.list_market_factors(days[2], "zz_test")
        assert [r.symbol for r in loaded] == [a]
        assert loaded[0].scores == {"flow": 70.0, "trend": None}
        assert loaded[0].metrics["trend_aligned"] is True
        assert loaded[0].highlights == ["投信連買 3 日"]
        assert await repo.latest_market_factor_date("zz_test") == days[2]
        assert await repo.latest_market_factor_date("zz_test", as_of=days[1]) == days[1]
        assert await repo.prune_market_factors("zz_test", 2) == 1
        assert await repo.list_market_factors(days[0], "zz_test") == []
        assert await repo.prune_market_factors("zz_test", 2) == 0
        old = snapshot(a, days[2]).model_copy(update={"version": "zz_old"})
        assert await repo.replace_market_factors(days[2], "zz_old", [old]) == 1
        assert await repo.delete_other_factor_versions("zz_test") >= 1
        assert await repo.list_market_factors(days[2], "zz_old") == []
        assert [r.symbol for r in await repo.list_market_factors(days[2], "zz_test")] == [a]


# ----------------------------------------------------- market page aggregates (perf)
async def _seed_random_market(session, seed: int):
    """The same randomised whole-market rows in Postgres and in the in-memory repository."""
    from market_perf_fixture import build_industries
    from test_market_perf import _random_market

    from quant_signal.infrastructure.memory import MemoryQuantRepository

    rows = [
        row.model_copy(update={"symbol": "ZZ" + row.symbol}) for row in _random_market(seed)
    ]
    industries = [
        item.model_copy(update={"symbol": "ZZ" + item.symbol}) for item in build_industries(45)
    ]
    repo = PostgresQuantRepository(session)
    await repo.upsert_market_stock_days(rows)
    await repo.upsert_market_industries(industries)
    memory = MemoryQuantRepository()
    await memory.upsert_market_stock_days(rows)
    await memory.upsert_market_industries(industries)
    return repo, memory


@pytest.mark.parametrize("seed", [1, 2])
async def test_sql_ranking_aggregate_matches_the_in_memory_contract(sessions, seed) -> None:
    from market_perf_fixture import sessions as session_dates
    from test_market_perf import METRICS

    async with sessions() as session:
        pg, memory = await _seed_random_market(session, seed)
        days = session_dates(14)
        checked = 0
        for metric in METRICS:
            for market in ("ALL", "TW", "TWO"):
                for window in (1, 3, 8):
                    for order in ("desc", "asc"):
                        for as_of in (None, days[-3]):
                            args = (metric, market, window, order, as_of)
                            results = []
                            for repo in (pg, memory):
                                ranking = await MarketRankingService(repo).ranking(
                                    metric,  # type: ignore[arg-type]
                                    market=market,  # type: ignore[arg-type]
                                    window=window,
                                    order=order,  # type: ignore[arg-type]
                                    limit=15,
                                    as_of=as_of,
                                )
                                results.append(ranking)
                            sql, python = results
                            assert [e.symbol for e in sql.entries] == [
                                e.symbol for e in python.entries
                            ], args
                            for a, b in zip(sql.entries, python.entries, strict=True):
                                assert a.value == pytest.approx(b.value, rel=1e-9, abs=1e-9), args
                                assert (a.close, a.day_change_pct, a.volume_lots) == (
                                    b.close,
                                    b.day_change_pct,
                                    b.volume_lots,
                                ), args
                                assert a.turnover_value == pytest.approx(b.turnover_value)
                            assert (sql.data_through, sql.window_start, sql.trading_days) == (
                                python.data_through,
                                python.window_start,
                                python.trading_days,
                            ), args
                            assert sql.awaiting_report_date == python.awaiting_report_date, args
                            checked += 1
        assert checked == 9 * 3 * 3 * 2 * 2


async def test_sql_change_pct_uses_the_oldest_days_reference_price(sessions) -> None:
    a = _symbol()
    rows = [
        _market_row(a, Z1, close=100.0, change=10.0),  # reference 90
        _market_row(a, Z2, close=95.0, change=-5.0),
        _market_row(a, Z3, close=99.0, change=4.0),
    ]
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        await repo.upsert_market_stock_days(rows)
        ranking = await MarketRankingService(repo).ranking("change_pct", window=3, as_of=Z3)
    assert [(e.symbol, e.value) for e in ranking.entries] == [(a, pytest.approx(10.0))]


async def test_market_overview_queries_agree_with_the_in_memory_repository(sessions) -> None:
    from market_perf_fixture import sessions as session_dates

    async with sessions() as session:
        pg, memory = await _seed_random_market(session, 3)
        days = session_dates(14)

        status = {s.market: s for s in await pg.market_data_status()}
        expected = {s.market: s for s in await memory.market_data_status()}
        assert status["TW"].last_date >= days[-1]  # the table may hold other rows
        for market, item in expected.items():
            assert status[market].stocks_on_last_day == item.stocks_on_last_day
            assert status[market].institutional_coverage == pytest.approx(
                item.institutional_coverage
            )
        assert await pg.market_session_summary("TWO") == (14, days[-1])

        for market in (None, "TW", "TWO"):
            for require in (None, "total_net", "margin_change"):
                assert await pg.list_market_trading_dates(
                    market, as_of=None, limit=14, require=require
                ) == await memory.list_market_trading_dates(
                    market, as_of=None, limit=14, require=require
                )

        sql_flows = await pg.market_flow_totals(days, None)
        py_flows = await memory.market_flow_totals(days, None)
        assert len(sql_flows) == len(py_flows) == 14
        for a, b in zip(sql_flows, py_flows, strict=True):
            for field in type(a).model_fields:
                x, y = getattr(a, field), getattr(b, field)
                assert x == pytest.approx(y) if isinstance(x, float) else x == y, field

        lean_sql = await pg.list_market_sector_days(days[-3:], as_of=days[-2])
        lean_py = await memory.list_market_sector_days(days[-3:], as_of=days[-2])
        assert sorted(map(tuple, lean_sql)) == sorted(lean_py)


async def test_data_version_changes_only_when_the_newest_rows_change(sessions) -> None:
    symbol = _symbol()
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        await repo.upsert_market_stock_days([_market_row(symbol, Z1), _market_row(symbol, Z2)])
        first = await repo.market_data_version()
        assert first.newest_date is not None and first.newest_date >= Z2
        assert await repo.market_data_version() == first  # a read changes nothing

        await repo.upsert_market_stock_days([_market_row(_symbol(), Z3)])
        added = await repo.market_data_version()
        assert added != first and added.newest_date >= Z3

        later = _market_row(symbol, Z3).model_copy(
            update={"available_at": datetime.now(UTC) + timedelta(hours=1)}
        )
        await repo.upsert_market_stock_days([later])
        assert await repo.market_data_version() != added  # a late update to a row counts
