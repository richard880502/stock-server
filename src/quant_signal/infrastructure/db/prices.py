"""Storage for intraday prices: ``minute_bars`` and ``quote_latest``.

Writers (the ingest service only) and the read service share these helpers so the
encoding lives in one place: prices are integer hundredths, minute volume is the
minute's own volume (lots for stocks, contracts for futures), symbols map to small
integer ids.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from quant_signal.infrastructure.db.models import MarketSymbolRow, MinuteBarRow, QuoteLatestRow
from quant_signal.infrastructure.db.partitions import (
    PARENT,
    add_months,
    create_partition_sql,
    expired_partitions,
    month_start,
)

# Postgres caps one statement at 65,535 bind parameters; 8 columns x 2,000 rows is safe.
CHUNK = 2000


def to_cents(price: float) -> int:
    """Price -> integer hundredths. Taiwan stock and futures prices have <= 2 decimals."""
    if not math.isfinite(price) or price <= 0:
        raise ValueError("price must be a positive finite number")
    return round(price * 100)


def from_cents(value: int | None) -> float | None:
    return None if value is None else value / 100


@dataclass(frozen=True)
class MinuteBar:
    symbol: str
    minute: datetime  # start of the minute, tz-aware
    open: float
    high: float
    low: float
    close: float
    volume: int  # the minute's own volume: lots (stocks) or contracts (futures)
    official: bool


@dataclass(frozen=True)
class LatestQuote:
    symbol: str
    price: float
    reference: float | None  # exchange reference price (平盤價) for the trade day
    open: float | None
    high: float | None
    low: float | None
    volume: int | None  # cumulative for the day, lots/contracts
    amount: int | None  # cumulative turnover, TWD
    trade_time: datetime
    source: str  # "tick" or "snapshot"
    updated_at: datetime

    @property
    def change_pct(self) -> float | None:
        if not self.reference:
            return None
        return self.price / self.reference - 1


class SymbolIds:
    """symbol <-> id, cached for the life of the process (ids never change)."""

    def __init__(self) -> None:
        self._ids: dict[str, int] = {}
        self._symbols: dict[int, str] = {}

    async def ids(self, session: AsyncSession, symbols: Iterable[str]) -> dict[str, int]:
        wanted = {s.strip().upper() for s in symbols}
        missing = wanted - self._ids.keys()
        if missing:
            await session.execute(
                insert(MarketSymbolRow)
                .values([{"symbol": s} for s in sorted(missing)])
                .on_conflict_do_nothing(index_elements=["symbol"])
            )
            rows = await session.execute(
                select(MarketSymbolRow.id, MarketSymbolRow.symbol).where(
                    MarketSymbolRow.symbol.in_(missing)
                )
            )
            for row_id, symbol in rows:
                self._ids[symbol] = row_id
                self._symbols[row_id] = symbol
        return {s: self._ids[s] for s in wanted if s in self._ids}

    async def lookup(self, session: AsyncSession, symbols: Iterable[str]) -> dict[str, int]:
        """Existing ids only (readers never create symbols)."""
        wanted = {s.strip().upper() for s in symbols}
        missing = wanted - self._ids.keys()
        if missing:
            rows = await session.execute(
                select(MarketSymbolRow.id, MarketSymbolRow.symbol).where(
                    MarketSymbolRow.symbol.in_(missing)
                )
            )
            for row_id, symbol in rows:
                self._ids[symbol] = row_id
                self._symbols[row_id] = symbol
        return {s: self._ids[s] for s in wanted if s in self._ids}


SYMBOL_IDS = SymbolIds()


def _minute_rows(bars: Sequence[MinuteBar], ids: dict[str, int]) -> list[dict]:
    return [
        {
            "minute": bar.minute,
            "open": to_cents(bar.open),
            "high": to_cents(bar.high),
            "low": to_cents(bar.low),
            "close": to_cents(bar.close),
            "volume": max(0, int(bar.volume)),
            "symbol_id": ids[bar.symbol],
            "official": bar.official,
        }
        for bar in bars
        if bar.symbol in ids
    ]


def live_minutes_statement(rows: list[dict]):
    """Merge live minutes; official rows are never touched.

    The collector drops out-of-order ticks, so the incoming close is the latest. It
    sends only the volume added since its previous write of the minute, which is
    added to the stored volume (so a restarted collector continues the count).
    """
    statement = insert(MinuteBarRow).values(rows)
    incoming, current = statement.excluded, MinuteBarRow
    return statement.on_conflict_do_update(
        index_elements=["symbol_id", "minute"],
        set_={
            "high": func.greatest(current.high, incoming.high),
            "low": func.least(current.low, incoming.low),
            "close": incoming.close,
            "volume": current.volume + incoming.volume,
        },
        where=current.official.is_(False),
    )


def official_minutes_statement(rows: list[dict], *, replace: bool):
    """Official minutes: fill gaps only, or (after the close) replace live rows."""
    statement = insert(MinuteBarRow).values(rows)
    if not replace:
        return statement.on_conflict_do_nothing(index_elements=["symbol_id", "minute"])
    incoming = statement.excluded
    return statement.on_conflict_do_update(
        index_elements=["symbol_id", "minute"],
        set_={
            column: getattr(incoming, column)
            for column in ("open", "high", "low", "close", "volume", "official")
        },
    )


async def store_minutes(
    session: AsyncSession, bars: Sequence[MinuteBar], *, live: bool, replace: bool = False
) -> int:
    if not bars:
        return 0
    ids = await SYMBOL_IDS.ids(session, {bar.symbol for bar in bars})
    rows = _minute_rows(bars, ids)
    for index in range(0, len(rows), CHUNK):
        chunk = rows[index : index + CHUNK]
        statement = (
            live_minutes_statement(chunk)
            if live
            else official_minutes_statement(chunk, replace=replace)
        )
        await session.execute(statement)
    return len(rows)


def latest_statement(rows: list[dict]):
    """Upsert latest prices; an older trade never replaces a newer one."""
    statement = insert(QuoteLatestRow).values(rows)
    incoming, current = statement.excluded, QuoteLatestRow
    return statement.on_conflict_do_update(
        index_elements=["symbol"],
        set_={
            column: getattr(incoming, column)
            for column in (
                "trade_time",
                "updated_at",
                "volume",
                "amount",
                "price",
                "reference",
                "open",
                "high",
                "low",
                "source",
            )
        },
        where=current.trade_time <= incoming.trade_time,
    )


def _latest_row(quote: LatestQuote) -> dict:
    optional = lambda value: None if value is None else to_cents(value)  # noqa: E731
    return {
        "symbol": quote.symbol,
        "trade_time": quote.trade_time,
        "updated_at": quote.updated_at,
        "volume": quote.volume,
        "amount": quote.amount,
        "price": to_cents(quote.price),
        "reference": optional(quote.reference),
        "open": optional(quote.open),
        "high": optional(quote.high),
        "low": optional(quote.low),
        "source": quote.source,
    }


async def store_latest(session: AsyncSession, quotes: Sequence[LatestQuote]) -> int:
    # One row per symbol per statement: Postgres rejects a statement that touches a row twice.
    newest: dict[str, LatestQuote] = {}
    for quote in quotes:
        if quote.symbol not in newest or quote.trade_time >= newest[quote.symbol].trade_time:
            newest[quote.symbol] = quote
    rows = [_latest_row(q) for q in newest.values()]
    for index in range(0, len(rows), CHUNK):
        await session.execute(latest_statement(rows[index : index + CHUNK]))
    return len(rows)


async def read_minutes(
    session: AsyncSession,
    symbol: str,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    limit: int | None = None,
    newest_first: bool = False,
) -> list[MinuteBar]:
    symbol = symbol.strip().upper()
    ids = await SYMBOL_IDS.lookup(session, [symbol])
    if symbol not in ids:
        return []
    statement = select(MinuteBarRow).where(MinuteBarRow.symbol_id == ids[symbol])
    if start is not None:
        statement = statement.where(MinuteBarRow.minute >= start)
    if end is not None:
        statement = statement.where(MinuteBarRow.minute < end)
    order = MinuteBarRow.minute.desc() if newest_first else MinuteBarRow.minute
    statement = statement.order_by(order)
    if limit is not None:
        statement = statement.limit(limit)
    rows = (await session.scalars(statement)).all()
    return [
        MinuteBar(
            symbol=symbol,
            minute=row.minute,
            open=row.open / 100,
            high=row.high / 100,
            low=row.low / 100,
            close=row.close / 100,
            volume=row.volume,
            official=row.official,
        )
        for row in rows
    ]


async def read_latest(
    session: AsyncSession, symbols: Iterable[str] | None = None
) -> dict[str, LatestQuote]:
    statement = select(QuoteLatestRow)
    if symbols is not None:
        wanted = sorted({s.strip().upper() for s in symbols})
        if not wanted:
            return {}
        statement = statement.where(QuoteLatestRow.symbol.in_(wanted))
    rows = (await session.scalars(statement)).all()
    return {
        row.symbol: LatestQuote(
            symbol=row.symbol,
            price=row.price / 100,
            reference=from_cents(row.reference),
            open=from_cents(row.open),
            high=from_cents(row.high),
            low=from_cents(row.low),
            volume=row.volume,
            amount=row.amount,
            trade_time=row.trade_time,
            source=row.source,
            updated_at=row.updated_at,
        )
        for row in rows
    }


async def ensure_partitions(session: AsyncSession, today: date, months_ahead: int = 2) -> None:
    """Create this month's and the next ``months_ahead`` months' partitions (idempotent)."""
    current = month_start(today)
    for offset in range(months_ahead + 1):
        await session.execute(text(create_partition_sql(add_months(current, offset))))


async def drop_expired_partitions(
    session: AsyncSession, today: date, keep_months: int
) -> list[str]:
    """Drop whole months older than the retention window (0 keeps everything)."""
    if keep_months <= 0:
        return []
    names = (
        await session.scalars(
            text(
                "SELECT c.relname FROM pg_inherits i "
                "JOIN pg_class c ON c.oid = i.inhrelid "
                "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = :parent"
            ),
            {"parent": PARENT},
        )
    ).all()
    expired = expired_partitions(list(names), today, keep_months)
    for name in expired:
        await session.execute(text(f"DROP TABLE IF EXISTS {name}"))
    return expired
