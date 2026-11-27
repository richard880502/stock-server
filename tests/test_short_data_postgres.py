"""Short-lending / day-trade / pledge / dividend storage against a real PostgreSQL.

Skipped unless ``QUANT_TEST_DATABASE_URL`` points at a migrated, dedicated database.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from quant_signal.domain.models import ForeignHolding, MarketStockDay
from quant_signal.domain.short_data import (
    DayTradeDay,
    DividendEvent,
    InsiderHolding,
    ShortLendingDay,
)
from quant_signal.infrastructure.db import PostgresQuantRepository

DATABASE_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="QUANT_TEST_DATABASE_URL not set")

TABLES = ("short_lending_daily", "day_trade_daily", "insider_holdings_monthly", "dividend_events")


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(DATABASE_URL)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    async with factory() as session:
        for table in (*TABLES, "market_stock_daily", "foreign_holding_daily"):
            await session.execute(text(f"DELETE FROM {table} WHERE symbol LIKE 'ZZ%'"))
        await session.commit()
    await engine.dispose()


async def test_storage_revisions_and_point_in_time_reads(sessions) -> None:
    symbol = f"ZZ{uuid4().hex[:8].upper()}.TW"
    sessions_list = [d for d in (date(2026, 10, 1) + timedelta(days=i) for i in range(60))]
    sessions_list = [d for d in sessions_list if d.weekday() < 5 and d <= date(2026, 11, 27)]
    first = datetime(2026, 11, 27, 15, tzinfo=UTC)
    later = first + timedelta(days=1)
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        await repo.upsert_market_stock_days(
            [
                MarketStockDay(
                    symbol=symbol,
                    name="測試",
                    market="TW",
                    trading_date=d,
                    open=10,
                    high=10,
                    low=10,
                    close=10,
                    volume=1_000_000,
                    turnover_value=1e7,
                    available_at=first,
                    source="test",
                )
                for d in sessions_list
            ]
        )
        sbl_rows = [
            ShortLendingDay(
                symbol=symbol,
                trading_date=d,
                source="twse_twt93u",
                sbl_sold=1000,
                sbl_returned=0,
                sbl_balance=100_000 + 1000 * i,
                available_at=first,
            )
            for i, d in enumerate(sessions_list)
        ]
        assert await repo.upsert_short_lending(sbl_rows) == len(sbl_rows)
        assert await repo.upsert_short_lending(sbl_rows) == 0  # idempotent
        changed = sbl_rows[-1].model_copy(update={"sbl_balance": 200_000, "available_at": later})
        assert await repo.upsert_short_lending([changed]) == 1  # revision 2
        day = sessions_list[-1]
        dt = DayTradeDay(
            symbol=symbol,
            trading_date=day,
            source="twse_twtb4u",
            day_trade_volume=250_000,
            day_trade_buy_amount=2_500_000,
            day_trade_sell_amount=2_510_000,
            available_at=first,
        )
        assert await repo.upsert_day_trade([dt]) == 1
        await repo.upsert_foreign_holding(
            [
                ForeignHolding(
                    symbol=symbol,
                    trading_date=day,
                    shares_outstanding=10_000_000,
                    foreign_shares=1,
                    foreign_holding_pct=0.1,
                    source="twse_qfiis",
                    available_at=first,
                )
            ]
        )

        # point in time: the restated balance is invisible before ``later``
        old = (await repo.latest_short_lending_metrics(first + timedelta(hours=1)))[symbol]
        new = (await repo.latest_short_lending_metrics(later + timedelta(hours=1)))[symbol]
        assert old.sbl_balance_shares == sbl_rows[-1].sbl_balance
        assert new.sbl_balance_shares == 200_000
        assert new.sbl_pct_of_shares == pytest.approx(2.0)
        assert new.days_to_cover == pytest.approx(0.2)
        assert new.sbl_sold_pct_of_volume == pytest.approx(0.1)
        assert new.sbl_change_5d_pct is not None
        trades = (await repo.latest_day_trade_metrics(later))[symbol]
        assert trades.day_trade_ratio_pct == pytest.approx(25.0)
        assert trades.day_trade_net_pnl_proxy == 10_000
        assert await repo.sd_latest_date("sbl", first - timedelta(days=60)) is None
        one = await repo.sd_sbl_rows(day, later + timedelta(hours=1), symbol)
        assert [r.revision for r in one] == [2]

        await repo.upsert_insider_holdings(
            [
                InsiderHolding(
                    symbol=symbol,
                    period="2026-10",
                    source="twse_t187ap11",
                    insider_count=5,
                    holding_shares=1_000_000,
                    pledged_shares=250_000,
                    pledge_ratio_pct=25.0,
                    published_on=date(2026, 11, 20),
                    available_at=first,
                )
            ]
        )
        pledge = (await repo.latest_insider_pledges(later))[symbol]
        assert pledge.pledge_ratio_pct == 25.0 and pledge.period == "2026-10"
        assert symbol not in await repo.latest_insider_pledges(first - timedelta(days=30))

        event = DividendEvent(
            symbol=symbol,
            ex_date=date(2026, 12, 10),
            kind="ex_rights_dividend",
            source="twse_twt48u",
            cash_dividend_per_share=1.5,
            stock_dividend_per_share=0.05,
            available_at=first,
        )
        assert await repo.upsert_dividend_events([event]) == 1
        assert await repo.upsert_dividend_events([event]) == 0
        revised = event.model_copy(update={"cash_dividend_per_share": 1.6, "available_at": later})
        assert await repo.upsert_dividend_events([revised]) == 1
        upcoming = (await repo.next_dividend_events(later))[symbol]
        assert upcoming.date == date(2026, 12, 10) and upcoming.cash_dividend == 1.6
        window = await repo.list_dividend_events(
            [symbol], date(2026, 12, 1), date(2026, 12, 31), first + timedelta(hours=1)
        )
        assert [e.cash_dividend_per_share for e in window] == [1.5]
