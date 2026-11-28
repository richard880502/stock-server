"""Shared fixtures for the paper-trading tests (no network, in-memory repository)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from quant_signal.domain.etf import EtfDay
from quant_signal.domain.models import MarketObservation, MarketStockDay
from quant_signal.infrastructure.db.prices import LatestQuote, MinuteBar
from quant_signal.infrastructure.memory import MemoryQuantRepository

TAIPEI = ZoneInfo("Asia/Taipei")
THU = date(2026, 11, 26)
FRI = date(2026, 11, 27)
MON = date(2026, 11, 30)
TUE = date(2026, 12, 1)


def at(day: date, hh: int, mm: int = 0, ss: int = 0) -> datetime:
    """A Taipei wall-clock instant (tz-aware)."""
    return datetime.combine(day, time(hh, mm, ss), TAIPEI)


def stock_row(
    symbol: str,
    day: date,
    *,
    close: float,
    name: str,
    change: float = 0.0,
    open_: float | None = None,
) -> MarketStockDay:
    return MarketStockDay(
        symbol=symbol,
        name=name,
        market="TWO" if symbol.endswith(".TWO") else "TW",
        trading_date=day,
        open=open_ or close,
        high=close,
        low=close,
        close=close,
        volume=10_000_000,
        turnover_value=close * 10_000_000,
        change=change,
        available_at=datetime.combine(day, time(10), tzinfo=UTC),
        source="twse_official",
    )


def etf_row(
    symbol: str,
    day: date,
    *,
    close: float,
    name: str,
    change: float = 0.0,
    open_: float | None = None,
) -> EtfDay:
    return EtfDay(
        symbol=symbol,
        name=name,
        market="TWO" if symbol.endswith(".TWO") else "TW",
        trading_date=day,
        open=open_ or close,
        high=close,
        low=close,
        close=close,
        change=change,
        volume=10_000_000,
        turnover_value=close * 10_000_000,
        trades=1000,
        available_at=datetime.combine(day, time(10), tzinfo=UTC),
        source="twse_official",
    )


def quote(
    symbol: str,
    price: float,
    when: datetime,
    *,
    reference: float | None = 100.0,
    open_: float | None = None,
    source: str = "tick",
) -> LatestQuote:
    return LatestQuote(
        symbol=symbol,
        price=price,
        reference=reference,
        open=open_,
        high=None,
        low=None,
        volume=1000,
        amount=None,
        trade_time=when,
        source=source,
        updated_at=when,
    )


def bar(symbol: str, minute: datetime, o: float, h: float, low: float, c: float, *, official=False):
    return MinuteBar(
        symbol=symbol, minute=minute, open=o, high=h, low=low, close=c, volume=10, official=official
    )


async def market_repo() -> MemoryQuantRepository:
    """1101.TW (ref 100), 0050.TW (ETF), 6488.TWO; official closes for Wed-Thu 11/25-26."""
    repo = MemoryQuantRepository()
    rows = []
    for day in (date(2026, 11, 25), THU):
        rows.append(stock_row("1101.TW", day, close=100.0, name="台泥"))
        rows.append(stock_row("6488.TWO", day, close=700.0, name="環球晶"))
    await repo.upsert_market_stock_days(rows)
    # ETFs live in their own table; they are never part of the whole-market stock table.
    await repo.upsert_etf_days(
        [
            etf_row("0050.TW", day, close=150.0, name="元大台灣50")
            for day in (date(2026, 11, 25), THU)
        ]
    )
    return repo


def taiex(day: date, close: float) -> MarketObservation:
    return MarketObservation(
        market="TW",
        trading_date=day,
        index_close=close,
        total_issues=1000,
        advancing_issues=500,
        declining_issues=480,
        unchanged_issues=20,
        above_ma20_issues=600,
        above_ma60_issues=500,
        new_high_52w_issues=50,
        new_low_52w_issues=10,
        up_volume=1.0,
        down_volume=1.0,
        turnover_value=1.0,
        foreign_net_flow=0.0,
        investment_trust_net_flow=0.0,
        dealer_net_flow=0.0,
        futures_net_open_interest=0.0,
        margin_balance=1.0,
        short_balance=1.0,
        available_at=datetime.combine(day, time(10), tzinfo=UTC),
        source="test",
    )


class FakeDemand:
    """A bounded stand-in for the Redis demand set."""

    def __init__(self, capacity: int, preloaded: tuple[str, ...] = ()) -> None:
        self.capacity = capacity
        self.symbols: set[str] = set(preloaded)

    async def admit(self, symbol: str) -> bool:
        if symbol in self.symbols:
            return True
        if len(self.symbols) >= self.capacity:
            return False
        self.symbols.add(symbol)
        return True


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def new_user() -> UUID:
    return uuid4()


def dec(value: str) -> Decimal:
    return Decimal(value)
