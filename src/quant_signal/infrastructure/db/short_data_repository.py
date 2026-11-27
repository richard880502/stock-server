"""Postgres storage for short-lending, day-trade, insider-pledge and dividend-event rows."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from quant_signal.domain.short_data import (
    DayTradeDay,
    DividendEvent,
    InsiderHolding,
    ShortLendingDay,
)
from quant_signal.infrastructure.db.models import (
    DayTradeDailyRow,
    DividendEventRow,
    InsiderHoldingMonthlyRow,
    MarketStockDayRow,
    ShortLendingDailyRow,
)
from quant_signal.infrastructure.short_data_reads import ShortDataReadsMixin, plan_revisions

CHUNK = 500


class ShortDataRepositoryMixin(ShortDataReadsMixin):
    session: AsyncSession

    async def _newest(self, row_cls: Any, model: type[BaseModel], keys: list[str], *where: Any):
        """Newest revision per business key among rows matching ``where``."""
        ranked = (
            select(
                row_cls,
                func.row_number()
                .over(
                    partition_by=[getattr(row_cls, k) for k in keys],
                    order_by=row_cls.revision.desc(),
                )
                .label("rn"),
            )
            .where(*where)
            .subquery()
        )
        row = aliased(row_cls, ranked)
        statement = select(row).where(ranked.c.rn == 1)
        fields = model.model_fields.keys()
        return [
            model.model_validate({name: getattr(item, name) for name in fields})
            for item in (await self.session.execute(statement)).scalars()
        ]

    async def _write(
        self, row_cls: Any, model: type[BaseModel], rows: list, keys: list[str], *where
    ):
        if not rows:
            return 0
        existing = {
            tuple(getattr(item, k) for k in keys): item
            for item in await self._newest(row_cls, model, keys, *where)
        }
        fresh = plan_revisions(rows, existing, lambda r: tuple(getattr(r, k) for k in keys))
        written = 0
        for offset in range(0, len(fresh), CHUNK):
            values = [r.model_dump(mode="python") for r in fresh[offset : offset + CHUNK]]
            statement = insert(row_cls).values(values).on_conflict_do_nothing()
            written += (await self.session.execute(statement)).rowcount or 0
        await self.session.commit()
        return written

    async def upsert_short_lending(self, rows: list[ShortLendingDay]) -> int:
        """Insert new (symbol, date) rows, or a new revision when the numbers changed."""
        cls = ShortLendingDailyRow
        days = {r.trading_date for r in rows}
        return await self._write(
            cls, ShortLendingDay, rows, ["symbol", "trading_date"], cls.trading_date.in_(days)
        )

    async def upsert_day_trade(self, rows: list[DayTradeDay]) -> int:
        cls = DayTradeDailyRow
        days = {r.trading_date for r in rows}
        return await self._write(
            cls, DayTradeDay, rows, ["symbol", "trading_date"], cls.trading_date.in_(days)
        )

    async def upsert_insider_holdings(self, rows: list[InsiderHolding]) -> int:
        cls = InsiderHoldingMonthlyRow
        periods = {r.period for r in rows}
        return await self._write(
            cls, InsiderHolding, rows, ["symbol", "period"], cls.period.in_(periods)
        )

    async def upsert_dividend_events(self, rows: list[DividendEvent]) -> int:
        cls = DividendEventRow
        if not rows:
            return 0
        first = min(r.ex_date for r in rows)
        return await self._write(
            cls, DividendEvent, rows, ["symbol", "ex_date", "kind"], cls.ex_date >= first
        )

    async def sd_sbl_rows(
        self, since: date, cutoff: datetime, symbol: str | None = None
    ) -> list[ShortLendingDay]:
        cls = ShortLendingDailyRow
        return await self._newest(
            cls,
            ShortLendingDay,
            ["symbol", "trading_date"],
            cls.trading_date >= since,
            cls.available_at <= cutoff,
            *([cls.symbol == symbol] if symbol else []),
        )

    async def sd_day_trade_rows(
        self, since: date, cutoff: datetime, symbol: str | None = None
    ) -> list[DayTradeDay]:
        cls = DayTradeDailyRow
        return await self._newest(
            cls,
            DayTradeDay,
            ["symbol", "trading_date"],
            cls.trading_date >= since,
            cls.available_at <= cutoff,
            *([cls.symbol == symbol] if symbol else []),
        )

    async def sd_latest_date(self, kind: str, cutoff: datetime) -> date | None:
        cls = ShortLendingDailyRow if kind == "sbl" else DayTradeDailyRow
        statement = select(func.max(cls.trading_date)).where(cls.available_at <= cutoff)
        return (await self.session.execute(statement)).scalar()

    async def sd_volume_rows(
        self, since: date, cutoff: datetime, symbol: str | None = None
    ) -> list[tuple[str, date, int]]:
        cls = MarketStockDayRow
        statement = select(cls.symbol, cls.trading_date, cls.volume).where(
            cls.trading_date >= since,
            cls.trading_date <= cutoff.date(),
            cls.available_at <= cutoff,
        )
        if symbol:
            statement = statement.where(cls.symbol == symbol)
        return [(s, d, int(v)) for s, d, v in (await self.session.execute(statement)).all()]

    async def sd_insider_rows(
        self, cutoff: datetime, symbol: str | None = None
    ) -> list[InsiderHolding]:
        cls = InsiderHoldingMonthlyRow
        rows = await self._newest(
            cls,
            InsiderHolding,
            ["symbol", "period"],
            cls.available_at <= cutoff,
            *([cls.symbol == symbol] if symbol else []),
        )
        newest: dict[str, InsiderHolding] = {}
        for row in rows:
            if row.symbol not in newest or row.period > newest[row.symbol].period:
                newest[row.symbol] = row
        return list(newest.values())

    async def sd_dividend_rows(
        self, start: date, end: date, cutoff: datetime
    ) -> list[DividendEvent]:
        cls = DividendEventRow
        return await self._newest(
            cls,
            DividendEvent,
            ["symbol", "ex_date", "kind"],
            cls.ex_date >= start,
            cls.ex_date <= end,
            cls.available_at <= cutoff,
        )
