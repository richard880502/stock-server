"""Postgres storage for ETF end-of-day quotes (``etf_daily``)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from quant_signal.domain.etf import EtfDay
from quant_signal.domain.models import Instrument
from quant_signal.infrastructure.db.etf_models import EtfDailyRow

ETF_CHUNK = 500
# An ETF that did not trade on the newest day (a ``--`` row is not stored) still resolves.
ETF_SEARCH_WINDOW_DAYS = 20


class EtfRepositoryMixin:
    session: AsyncSession

    async def upsert_etf_days(self, rows: list[EtfDay]) -> int:
        if not rows:
            return 0
        written = 0
        for offset in range(0, len(rows), ETF_CHUNK):
            statement = insert(EtfDailyRow).values(
                [item.model_dump(mode="python") for item in rows[offset : offset + ETF_CHUNK]]
            )
            excluded = statement.excluded
            statement = statement.on_conflict_do_update(
                index_elements=["symbol", "trading_date"],
                set_={
                    column: getattr(excluded, column)
                    for column in EtfDay.model_fields
                    if column not in ("symbol", "trading_date")
                },
            )
            written += (await self.session.execute(statement)).rowcount or 0
        await self.session.commit()
        return written

    async def list_etf_rows(
        self, symbols: list[str], *, as_of: date | None = None, sessions: int = 3
    ) -> list[EtfDay]:
        """Each symbol's newest ``sessions`` rows on or before ``as_of`` (oldest first)."""
        wanted = sorted({item.upper() for item in symbols})
        if not wanted or sessions < 1:
            return []
        row = EtfDailyRow
        filters = [row.symbol.in_(wanted)]
        if as_of is not None:
            filters.append(row.trading_date <= as_of)
        ranked = (
            select(
                row.symbol,
                row.trading_date,
                func.row_number()
                .over(partition_by=row.symbol, order_by=row.trading_date.desc())
                .label("etf_rank"),
            )
            .where(*filters)
            .subquery()
        )
        statement = (
            select(row)
            .join(
                ranked,
                and_(row.symbol == ranked.c.symbol, row.trading_date == ranked.c.trading_date),
            )
            .where(ranked.c.etf_rank <= sessions)
            .order_by(row.symbol, row.trading_date)
        )
        return [
            EtfDay.model_validate({name: getattr(item, name) for name in EtfDay.model_fields})
            for item in (await self.session.execute(statement)).scalars().all()
        ]

    async def search_etf_instruments(self, query: str, *, limit: int = 10) -> list[Instrument]:
        """ETFs whose code or name contains ``query`` (newest stored name per symbol)."""
        needle = query.strip()
        if not needle:
            return []
        row = EtfDailyRow
        newest = select(func.max(row.trading_date)).scalar_subquery()
        pattern = f"%{needle}%"
        statement = (
            select(row.symbol, row.name, row.market)
            .where(
                row.trading_date >= newest - timedelta(days=ETF_SEARCH_WINDOW_DAYS),
                or_(row.symbol.ilike(pattern), row.name.ilike(pattern)),
            )
            .distinct(row.symbol)
            .order_by(row.symbol, row.trading_date.desc())
            .limit(max(limit, 1) * 5)
        )
        return [
            Instrument(symbol=symbol, name=name, market=market, tracked=False, kind="etf")
            for symbol, name, market in (await self.session.execute(statement)).all()
        ]
