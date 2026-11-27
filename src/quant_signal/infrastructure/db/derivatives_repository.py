"""Postgres storage for the TAIFEX derivatives datasets."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from quant_signal.domain.derivatives import (
    DerivativesRows,
    FuturesInstitutionalDay,
    FuturesLargeTradersDay,
    OptionsInstitutionalDay,
    OptionsOiLevelDay,
    OptionsPcRatioDay,
    StockFuturesDay,
    VixDay,
)
from quant_signal.infrastructure.db.derivatives_models import (
    FuturesInstitutionalRow,
    FuturesLargeTradersRow,
    OptionsInstitutionalRow,
    OptionsOiLevelRow,
    OptionsPcRatioRow,
    StockFuturesRow,
    VixRow,
)
from quant_signal.infrastructure.derivatives_queries import DerivativesQueryMixin, _convert

CHUNK = 500


class DerivativesRepositoryMixin(DerivativesQueryMixin):
    session: AsyncSession

    async def _insert_rows(self, row_cls: Any, rows: list[BaseModel]) -> int:
        """Published rows are final: a repeated key is ignored, never rewritten."""
        written = 0
        for offset in range(0, len(rows), CHUNK):
            values = [row.model_dump(mode="python") for row in rows[offset : offset + CHUNK]]
            statement = insert(row_cls).values(values).on_conflict_do_nothing()
            written += (await self.session.execute(statement)).rowcount or 0
        await self.session.commit()
        return written

    async def upsert_futures_institutional(self, rows: list[FuturesInstitutionalDay]) -> int:
        return await self._insert_rows(FuturesInstitutionalRow, rows) if rows else 0

    async def upsert_options_pc_ratio(self, rows: list[OptionsPcRatioDay]) -> int:
        return await self._insert_rows(OptionsPcRatioRow, rows) if rows else 0

    async def upsert_options_institutional(self, rows: list[OptionsInstitutionalDay]) -> int:
        return await self._insert_rows(OptionsInstitutionalRow, rows) if rows else 0

    async def upsert_futures_large_traders(self, rows: list[FuturesLargeTradersDay]) -> int:
        return await self._insert_rows(FuturesLargeTradersRow, rows) if rows else 0

    async def upsert_stock_futures(self, rows: list[StockFuturesDay]) -> int:
        return await self._insert_rows(StockFuturesRow, rows) if rows else 0

    async def upsert_options_oi_levels(self, rows: list[OptionsOiLevelDay]) -> int:
        return await self._insert_rows(OptionsOiLevelRow, rows) if rows else 0

    async def upsert_vix_daily(self, rows: list[VixDay]) -> int:
        return await self._insert_rows(VixRow, rows) if rows else 0

    async def _newest(self, cls: Any, model: Any, as_of: datetime, days: int) -> list[Any]:
        """Rows of the newest ``days`` distinct trading dates visible at ``as_of``."""
        dates = (
            select(cls.trading_date)
            .where(cls.available_at <= as_of)
            .distinct()
            .order_by(cls.trading_date.desc())
            .limit(days)
        )
        statement = (
            select(cls)
            .where(cls.available_at <= as_of, cls.trading_date.in_(dates))
            .order_by(cls.trading_date)
        )
        return [_convert(model, row) for row in (await self.session.execute(statement)).scalars()]

    async def derivatives_rows(self, as_of: datetime, days: int) -> DerivativesRows:
        return DerivativesRows(
            futures=await self._newest(
                FuturesInstitutionalRow, FuturesInstitutionalDay, as_of, days
            ),
            pc_ratio=await self._newest(OptionsPcRatioRow, OptionsPcRatioDay, as_of, days),
            options=await self._newest(
                OptionsInstitutionalRow, OptionsInstitutionalDay, as_of, days
            ),
            large_traders=await self._newest(
                FuturesLargeTradersRow, FuturesLargeTradersDay, as_of, days
            ),
            vix=await self._newest(VixRow, VixDay, as_of, days),
            oi_levels=await self._newest(OptionsOiLevelRow, OptionsOiLevelDay, as_of, days),
        )

    async def futures_institutional_range(
        self, start: date, end: date
    ) -> list[FuturesInstitutionalDay]:
        cls = FuturesInstitutionalRow
        statement = (
            select(cls)
            .where(cls.trading_date >= start, cls.trading_date <= end)
            .order_by(cls.trading_date)
        )
        return [
            _convert(FuturesInstitutionalDay, row)
            for row in (await self.session.execute(statement)).scalars()
        ]

    async def stock_futures_history(
        self, symbol: str, as_of: datetime, days: int
    ) -> list[StockFuturesDay]:
        """The symbol's newest ``days`` visible rows, oldest first."""
        cls = StockFuturesRow
        statement = (
            select(cls)
            .where(cls.symbol == symbol.upper(), cls.available_at <= as_of)
            .order_by(cls.trading_date.desc())
            .limit(days)
        )
        rows = (await self.session.execute(statement)).scalars().all()
        return [_convert(StockFuturesDay, row) for row in reversed(rows)]

    async def has_stock_futures(self, symbol: str) -> bool:
        statement = (
            select(StockFuturesRow.symbol).where(StockFuturesRow.symbol == symbol.upper()).limit(1)
        )
        return (await self.session.execute(statement)).first() is not None

    async def latest_stock_futures_days(
        self, as_of: datetime, sessions: int = 6
    ) -> dict[str, list[StockFuturesDay]]:
        rows = await self._newest(StockFuturesRow, StockFuturesDay, as_of, sessions)
        out: dict[str, list[StockFuturesDay]] = {}
        for row in rows:
            out.setdefault(row.symbol, []).append(row)
        return out

    async def stock_spot_closes(self, symbol: str, start: date, end: date) -> dict[date, float]:
        from quant_signal.infrastructure.db.models import MarketStockDayRow

        row = MarketStockDayRow
        statement = select(row.trading_date, row.close).where(
            row.symbol == symbol.upper(), row.trading_date >= start, row.trading_date <= end
        )
        return {day: float(close) for day, close in (await self.session.execute(statement)).all()}
