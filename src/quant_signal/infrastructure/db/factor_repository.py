"""Postgres storage for the whole-market factor snapshot and its lean history feed."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from quant_signal.domain.models import MarketFactorSnapshot
from quant_signal.infrastructure.db.models import MarketFactorDailyRow, MarketStockDayRow

CHUNK = 400
STREAM_BATCH = 5000

HISTORY_COLUMNS = (
    MarketStockDayRow.trading_date,
    MarketStockDayRow.close,
    MarketStockDayRow.change,
    MarketStockDayRow.high,
    MarketStockDayRow.low,
    MarketStockDayRow.volume,
    MarketStockDayRow.turnover_value,
    MarketStockDayRow.foreign_net,
    MarketStockDayRow.trust_net,
    MarketStockDayRow.dealer_net,
    MarketStockDayRow.total_net,
    MarketStockDayRow.margin_balance,
    MarketStockDayRow.margin_change,
    MarketStockDayRow.short_balance,
    MarketStockDayRow.short_change,
)


class FactorDataRepositoryMixin:
    session: AsyncSession

    async def market_flow_through(self, end: date) -> dict[str, date]:
        """Per market, the newest session ≤ ``end`` with a published institutional report."""
        statement = (
            select(MarketStockDayRow.market, func.max(MarketStockDayRow.trading_date))
            .where(
                MarketStockDayRow.foreign_net.is_not(None), MarketStockDayRow.trading_date <= end
            )
            .group_by(MarketStockDayRow.market)
        )
        return {market: day for market, day in (await self.session.execute(statement)).all()}

    async def iter_market_histories(
        self, end: date, sessions: int
    ) -> AsyncIterator[tuple[str, str, str, list[tuple[Any, ...]]]]:
        """Stream ``(symbol, name, market, rows)`` for the last ``sessions`` dates ≤ ``end``.

        Rows are plain tuples (see ``HISTORY_COLUMNS``) sorted by date, delivered one
        symbol at a time from a server-side cursor, so the ~500k rows are never held
        together. Do not use the session for anything else until the iterator is drained.
        """
        dates = (
            select(MarketStockDayRow.trading_date)
            .where(MarketStockDayRow.trading_date <= end)
            .group_by(MarketStockDayRow.trading_date)
            .order_by(MarketStockDayRow.trading_date.desc())
            .limit(sessions)
        )
        oldest = (
            await self.session.execute(select(func.min(dates.subquery().c.trading_date)))
        ).scalar()
        if oldest is None:
            return
        statement = (
            select(
                MarketStockDayRow.symbol,
                MarketStockDayRow.name,
                MarketStockDayRow.market,
                *HISTORY_COLUMNS,
            )
            .where(MarketStockDayRow.trading_date >= oldest, MarketStockDayRow.trading_date <= end)
            .order_by(MarketStockDayRow.symbol, MarketStockDayRow.trading_date)
            .execution_options(yield_per=STREAM_BATCH)
        )
        result = await self.session.stream(statement)
        current: str | None = None
        meta: tuple[str, str] = ("", "")
        by_date: dict[date, tuple[Any, ...]] = {}
        async for row in result:
            if row[0] != current:
                if current is not None:
                    yield current, meta[0], meta[1], [by_date[d] for d in sorted(by_date)]
                current, by_date = row[0], {}
            meta = (row[1], row[2])
            by_date[row[3]] = tuple(row[3:])  # a later source for the same day wins
        if current is not None:
            yield current, meta[0], meta[1], [by_date[d] for d in sorted(by_date)]

    async def replace_market_factors(
        self, trading_date: date, version: str, rows: list[MarketFactorSnapshot]
    ) -> int:
        """Idempotently swap one date's snapshot for ``rows`` (one transaction)."""
        await self.session.execute(
            delete(MarketFactorDailyRow).where(
                MarketFactorDailyRow.trading_date == trading_date,
                MarketFactorDailyRow.version == version,
            )
        )
        for offset in range(0, len(rows), CHUNK):
            await self.session.execute(
                insert(MarketFactorDailyRow).values(
                    [item.model_dump(mode="python") for item in rows[offset : offset + CHUNK]]
                )
            )
        await self.session.commit()
        return len(rows)

    async def prune_market_factors(self, version: str, keep: int) -> int:
        """Keep the newest ``keep`` dates of ``version``; return the rows removed."""
        newest = (
            select(MarketFactorDailyRow.trading_date)
            .where(MarketFactorDailyRow.version == version)
            .group_by(MarketFactorDailyRow.trading_date)
            .order_by(MarketFactorDailyRow.trading_date.desc())
            .limit(keep)
            .subquery()
        )
        cutoff = (await self.session.execute(select(func.min(newest.c.trading_date)))).scalar()
        if cutoff is None:
            return 0
        result = await self.session.execute(
            delete(MarketFactorDailyRow).where(
                MarketFactorDailyRow.version == version,
                MarketFactorDailyRow.trading_date < cutoff,
            )
        )
        await self.session.commit()
        return result.rowcount or 0

    async def latest_market_factor_date(
        self, version: str, as_of: date | None = None
    ) -> date | None:
        statement = select(func.max(MarketFactorDailyRow.trading_date)).where(
            MarketFactorDailyRow.version == version
        )
        if as_of is not None:
            statement = statement.where(MarketFactorDailyRow.trading_date <= as_of)
        return (await self.session.execute(statement)).scalar()

    async def list_market_factors(
        self, trading_date: date, version: str
    ) -> list[MarketFactorSnapshot]:
        statement = select(MarketFactorDailyRow).where(
            MarketFactorDailyRow.trading_date == trading_date,
            MarketFactorDailyRow.version == version,
        )
        rows = (await self.session.execute(statement)).scalars().all()
        return [
            MarketFactorSnapshot.model_validate(
                {name: getattr(row, name) for name in MarketFactorSnapshot.model_fields}
            )
            for row in rows
        ]
