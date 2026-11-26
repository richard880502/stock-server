"""Postgres storage for official events, risk flags and monthly revenue.

Written only by the ingest service; the API and MCP only read. Every read is
point-in-time: a row is visible when ``available_at <= as_of`` and, among a key's
revisions, the highest visible revision wins.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from quant_signal.domain.models import MarketEvent, MonthlyRevenue, RiskFlag
from quant_signal.infrastructure.db.models import (
    MarketEventRow,
    MarketRiskFlagRow,
    MonthlyRevenueRow,
)
from quant_signal.infrastructure.revisions import plan_revisions

TAIPEI = ZoneInfo("Asia/Taipei")
CHUNK = 500

EVENT_KEY = ("symbol", "kind", "external_key")
FLAG_KEY = ("symbol", "kind", "start_date", "source")
REVENUE_KEY = ("symbol", "period")


def _key(*names: str) -> Callable[[Any], Hashable]:
    return lambda row: tuple(getattr(row, name) for name in names)


class OfficialDataRepositoryMixin:
    session: AsyncSession

    async def _latest_existing[M: BaseModel](
        self, row_cls: Any, model: type[M], key_names: tuple[str, ...], rows: list[M]
    ) -> dict[Hashable, M]:
        """Highest stored revision for each incoming natural key."""
        keys = list({tuple(getattr(row, name) for name in key_names) for row in rows})
        found: dict[Hashable, M] = {}
        columns = [getattr(row_cls, name) for name in key_names]
        for offset in range(0, len(keys), CHUNK):
            statement = select(row_cls).where(tuple_(*columns).in_(keys[offset : offset + CHUNK]))
            for stored in (await self.session.execute(statement)).scalars():
                item = model.model_validate(
                    {name: getattr(stored, name) for name in model.model_fields}
                )
                key = tuple(getattr(item, name) for name in key_names)
                if key not in found or item.revision > found[key].revision:  # type: ignore[attr-defined]
                    found[key] = item
        return found

    async def _insert_new(self, row_cls: Any, rows: list[BaseModel]) -> int:
        written = 0
        for offset in range(0, len(rows), CHUNK):
            values = [row.model_dump(mode="python") for row in rows[offset : offset + CHUNK]]
            statement = insert(row_cls).values(values).on_conflict_do_nothing()
            written += (await self.session.execute(statement)).rowcount or 0
        await self.session.commit()
        return written

    # --- writes ------------------------------------------------------------------

    async def upsert_market_events(self, rows: list[MarketEvent]) -> int:
        if not rows:
            return 0
        existing = await self._latest_existing(MarketEventRow, MarketEvent, EVENT_KEY, rows)
        planned = plan_revisions(
            existing, rows, _key(*EVENT_KEY), ignore={"announced_at"}, carry=("announced_at",)
        )
        return await self._insert_new(MarketEventRow, planned)

    async def upsert_risk_flags(self, rows: list[RiskFlag]) -> int:
        if not rows:
            return 0
        existing = await self._latest_existing(MarketRiskFlagRow, RiskFlag, FLAG_KEY, rows)
        planned = plan_revisions(existing, rows, _key(*FLAG_KEY))
        return await self._insert_new(MarketRiskFlagRow, planned)

    async def upsert_monthly_revenue(self, rows: list[MonthlyRevenue]) -> int:
        if not rows:
            return 0
        existing = await self._latest_existing(MonthlyRevenueRow, MonthlyRevenue, REVENUE_KEY, rows)
        planned = plan_revisions(existing, rows, _key(*REVENUE_KEY))
        return await self._insert_new(MonthlyRevenueRow, planned)

    # --- point-in-time reads -------------------------------------------------------

    @staticmethod
    def _rank(row_cls: Any, partition: tuple[str, ...], *order: Any) -> Any:
        return func.row_number().over(
            partition_by=[getattr(row_cls, name) for name in partition],
            order_by=list(order),
        )

    async def list_market_events(
        self, symbols: list[str] | None, since: datetime, as_of: datetime
    ) -> list[MarketEvent]:
        if symbols is not None and not symbols:
            return []
        cls = MarketEventRow
        inner = select(cls, self._rank(cls, EVENT_KEY, cls.revision.desc()).label("rn")).where(
            cls.available_at <= as_of
        )
        if symbols is not None:
            inner = inner.where(cls.symbol.in_([s.upper() for s in symbols]))
        ranked = inner.subquery()
        row = aliased(cls, ranked)
        since_day = since.astimezone(TAIPEI).date()
        statement = (
            select(row)
            .where(ranked.c.rn == 1)
            .where(
                # Schedules (ex-rights) stay relevant until their date passes.
                ((row.kind == "ex_rights") & (row.effective_date >= since_day))
                | (
                    (row.kind != "ex_rights")
                    & (row.announced_at >= since)
                    & (row.announced_at <= as_of)
                )
            )
            .order_by(row.announced_at.desc(), row.symbol)
        )
        rows = (await self.session.execute(statement)).scalars().all()
        return [
            MarketEvent.model_validate({n: getattr(r, n) for n in MarketEvent.model_fields})
            for r in rows
        ]

    async def active_risk_flags(
        self, as_of: datetime, symbols: list[str] | None = None
    ) -> list[RiskFlag]:
        if symbols is not None and not symbols:
            return []
        cls = MarketRiskFlagRow
        day: date = as_of.astimezone(TAIPEI).date()
        inner = select(cls, self._rank(cls, FLAG_KEY, cls.revision.desc()).label("rn")).where(
            cls.available_at <= as_of, cls.start_date <= day
        )
        if symbols is not None:
            inner = inner.where(cls.symbol.in_([s.upper() for s in symbols]))
        ranked = inner.subquery()
        row = aliased(cls, ranked)
        statement = (
            select(row)
            .where(ranked.c.rn == 1)
            .where((row.end_date.is_(None)) | (row.end_date >= day))
            .order_by(row.symbol, row.kind, row.start_date)
        )
        rows = (await self.session.execute(statement)).scalars().all()
        return [
            RiskFlag.model_validate({n: getattr(r, n) for n in RiskFlag.model_fields}) for r in rows
        ]

    async def monthly_revenue(
        self, symbol: str, as_of: datetime, months: int = 13
    ) -> list[MonthlyRevenue]:
        cls = MonthlyRevenueRow
        ranked = (
            select(cls, self._rank(cls, REVENUE_KEY, cls.revision.desc()).label("rn"))
            .where(cls.symbol == symbol.upper(), cls.available_at <= as_of)
            .subquery()
        )
        row = aliased(cls, ranked)
        statement = select(row).where(ranked.c.rn == 1).order_by(row.period.desc()).limit(months)
        rows = (await self.session.execute(statement)).scalars().all()
        items = [
            MonthlyRevenue.model_validate({n: getattr(r, n) for n in MonthlyRevenue.model_fields})
            for r in rows
        ]
        return sorted(items, key=lambda item: item.period)

    async def latest_monthly_revenue(self, as_of: datetime) -> list[MonthlyRevenue]:
        """Every company's newest visible month (highest visible revision)."""
        cls = MonthlyRevenueRow
        ranked = (
            select(
                cls,
                self._rank(cls, ("symbol",), cls.period.desc(), cls.revision.desc()).label("rn"),
            )
            .where(cls.available_at <= as_of)
            .subquery()
        )
        row = aliased(cls, ranked)
        statement = select(row).where(ranked.c.rn == 1).order_by(row.symbol)
        rows = (await self.session.execute(statement)).scalars().all()
        return [
            MonthlyRevenue.model_validate({n: getattr(r, n) for n in MonthlyRevenue.model_fields})
            for r in rows
        ]
