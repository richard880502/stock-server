"""Postgres storage for TDCC shareholding distribution and foreign holding."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from quant_signal.domain.models import (
    ForeignHolding,
    ShareholdingDistribution,
    ShareholdingPair,
    ShareholdingSnapshot,
)
from quant_signal.infrastructure.db.models import ForeignHoldingRow, ShareholdingDistributionRow
from quant_signal.quant.holders import PAIR_WINDOW, pair_snapshots

CHUNK = 500


class HoldersRepositoryMixin:
    session: AsyncSession

    async def _insert_rows(self, row_cls: Any, rows: list[BaseModel]) -> int:
        written = 0
        for offset in range(0, len(rows), CHUNK):
            values = [row.model_dump(mode="python") for row in rows[offset : offset + CHUNK]]
            statement = insert(row_cls).values(values).on_conflict_do_nothing()
            written += (await self.session.execute(statement)).rowcount or 0
        await self.session.commit()
        return written

    async def upsert_shareholding_distribution(self, rows: list[ShareholdingDistribution]) -> int:
        """Insert new (symbol, data_date) weeks; published weeks are final, so no update."""
        return await self._insert_rows(ShareholdingDistributionRow, rows) if rows else 0

    async def upsert_foreign_holding(self, rows: list[ForeignHolding]) -> int:
        return await self._insert_rows(ForeignHoldingRow, rows) if rows else 0

    async def latest_shareholding_pairs(self, as_of: datetime) -> dict[str, ShareholdingPair]:
        """Per symbol: newest visible week and the comparison week (see ``pair_snapshots``)."""
        cls = ShareholdingDistributionRow
        newest = (
            await self.session.execute(
                select(func.max(cls.data_date)).where(cls.available_at <= as_of)
            )
        ).scalar()
        if newest is None:
            return {}
        columns = [getattr(cls, name) for name in ShareholdingSnapshot.model_fields]
        statement = select(*columns).where(
            cls.available_at <= as_of,
            cls.data_date <= newest,
            cls.data_date >= newest - PAIR_WINDOW,
        )
        by_symbol: dict[str, list[ShareholdingSnapshot]] = {}
        for record in (await self.session.execute(statement)).mappings():
            row = ShareholdingSnapshot.model_validate(dict(record))
            by_symbol.setdefault(row.symbol, []).append(row)
        pairs = {symbol: pair_snapshots(rows) for symbol, rows in by_symbol.items()}
        return {symbol: pair for symbol, pair in sorted(pairs.items()) if pair is not None}

    async def latest_foreign_holdings(self, as_of: datetime) -> dict[str, ForeignHolding]:
        """Newest visible foreign-holding row per symbol (latest trading date)."""
        cls = ForeignHoldingRow
        ranked = (
            select(
                cls,
                func.row_number()
                .over(
                    partition_by=cls.symbol,
                    order_by=[cls.trading_date.desc(), cls.available_at.desc()],
                )
                .label("rn"),
            )
            .where(cls.available_at <= as_of)
            .subquery()
        )
        row = aliased(cls, ranked)
        statement = select(row).where(ranked.c.rn == 1).order_by(row.symbol)
        return {
            item.symbol: _foreign(item)
            for item in (await self.session.execute(statement)).scalars()
        }

    async def latest_foreign_holding(self, symbol: str, as_of: datetime) -> ForeignHolding | None:
        cls = ForeignHoldingRow
        statement = (
            select(cls)
            .where(cls.symbol == symbol.upper(), cls.available_at <= as_of)
            .order_by(cls.trading_date.desc(), cls.available_at.desc())
            .limit(1)
        )
        found = (await self.session.execute(statement)).scalars().first()
        return _foreign(found) if found else None

    async def shareholding_history(
        self, symbol: str, as_of: datetime, weeks: int = 26
    ) -> list[ShareholdingDistribution]:
        """The symbol's visible weekly rows, oldest first, at most the newest ``weeks``."""
        cls = ShareholdingDistributionRow
        statement = (
            select(cls)
            .where(cls.symbol == symbol.upper(), cls.available_at <= as_of)
            .order_by(cls.data_date.desc())
            .limit(weeks)
        )
        rows = (await self.session.execute(statement)).scalars().all()
        items = [
            ShareholdingDistribution.model_validate(
                {name: getattr(r, name) for name in ShareholdingDistribution.model_fields}
            )
            for r in rows
        ]
        return sorted(items, key=lambda item: item.data_date)


def _foreign(row: ForeignHoldingRow) -> ForeignHolding:
    return ForeignHolding.model_validate(
        {name: getattr(row, name) for name in ForeignHolding.model_fields}
    )
