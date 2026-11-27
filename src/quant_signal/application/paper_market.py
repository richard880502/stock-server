"""Price context for paper trading, read only from our own storage.

``quote_latest`` (live ticks / 10-minute snapshots), ``market_stock_daily`` (official
daily rows). Nothing here talks to a broker or the Shioaji SDK.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from quant_signal.application.paper_ports import PaperRepository
from quant_signal.domain.paper import PaperAccountRecord, SnapshotRecord
from quant_signal.infrastructure.db.prices import LatestQuote
from quant_signal.quant.paper_calendar import (
    SESSION_CLOSE,
    TAIPEI,
    in_session,
    local,
)
from quant_signal.quant.paper_matching import past_session_reference, to_price

D = Decimal
STALE_TICK_SECONDS = 120
VOLUME_SESSIONS = 20
SYMBOL_RE = re.compile(r"^\d{4,6}[A-Z]?(\.TWO?)?$")


@dataclass(frozen=True)
class MarketView:
    """Everything known about one symbol's prices right now."""

    symbol: str
    name: str | None
    market: str
    official_close: Decimal | None
    official_prev: Decimal | None
    official_open: Decimal | None
    official_date: date | None
    avg_volume_shares: float | None
    quote: LatestQuote | None

    @property
    def known(self) -> bool:
        return self.name is not None

    @property
    def quote_date(self) -> date | None:
        return None if self.quote is None else local(self.quote.trade_time).date()


@dataclass(frozen=True)
class PriceInfo:
    price: Decimal
    time: datetime | None
    delayed: bool
    reference: Decimal | None  # previous close for the day-change figure


def market_of(symbol: str) -> str:
    return "TWO" if symbol.endswith(".TWO") else "TW"


def delayed(quote: LatestQuote | None, now: datetime) -> bool:
    """Only meaningful while trades can print: a tick older than 2 minutes, a 10-minute
    snapshot or no quote at all means we are not following the symbol live."""
    if not in_session(now):
        return False
    if quote is None or quote.source != "tick":
        return True
    return (now - quote.trade_time).total_seconds() > STALE_TICK_SECONDS


def price_info(view: MarketView, now: datetime) -> PriceInfo | None:
    quote, official = view.quote, view.official_close
    official_date, quote_date = view.official_date, view.quote_date
    if official is not None and official_date is not None:
        if quote is None or quote_date is None or official_date >= quote_date:
            closed_at = datetime.combine(official_date, SESSION_CLOSE, TAIPEI)
            return PriceInfo(official, closed_at, delayed(quote, now), view.official_prev)
    if quote is not None:
        reference = (
            D(str(quote.reference))
            if quote.reference
            else (official if official is not None else None)
        )
        return PriceInfo(to_price(quote.price), quote.trade_time, delayed(quote, now), reference)
    return None


async def load_market(
    repo: PaperRepository, symbols: list[str], *, with_volume: bool = False
) -> dict[str, MarketView]:
    wanted = sorted({s.upper() for s in symbols})
    if not wanted:
        return {}
    dates = await repo.list_market_trading_dates(
        None, as_of=None, limit=VOLUME_SESSIONS if with_volume else 3
    )
    rows = await repo.list_market_stock_rows(wanted, dates) if dates else []
    quotes = await repo.latest_quotes(wanted)
    by_symbol: dict[str, list] = {}
    for row in rows:
        by_symbol.setdefault(row.symbol, []).append(row)
    views: dict[str, MarketView] = {}
    for symbol in wanted:
        history = sorted(by_symbol.get(symbol, []), key=lambda r: (r.trading_date, r.source))
        newest = history[-1] if history else None
        volumes = [r.volume for r in history]
        views[symbol] = MarketView(
            symbol=symbol,
            name=None if newest is None else newest.name,
            market=market_of(symbol) if newest is None else newest.market,
            official_close=None if newest is None else to_price(newest.close),
            official_prev=(
                None
                if newest is None
                else to_price(newest.close - newest.change)
                if newest.close - newest.change > 0
                else None
            ),
            official_open=None if newest is None else to_price(newest.open),
            official_date=None if newest is None else newest.trading_date,
            avg_volume_shares=(sum(volumes) / len(volumes)) if with_volume and volumes else None,
            quote=quotes.get(symbol),
        )
    return views


async def past_references(
    repo: PaperRepository, symbols: list[str], day: date
) -> dict[str, Decimal]:
    """Reference prices of the finished session ``day``, from official daily rows only.

    ``close - change`` of that day's own row, else the newest official close before it.
    Symbols with neither are left out.
    """
    wanted = sorted({s.upper() for s in symbols})
    if not wanted:
        return {}
    dates = await repo.list_market_trading_dates(None, as_of=day, limit=3)
    rows = await repo.list_market_stock_rows(wanted, dates) if dates else []
    by_symbol: dict[str, list] = {}
    for row in rows:
        by_symbol.setdefault(row.symbol, []).append(row)
    found: dict[str, Decimal] = {}
    for symbol, history in by_symbol.items():
        history.sort(key=lambda r: (r.trading_date, r.source))
        own = [r for r in history if r.trading_date == day]
        before = [r for r in history if r.trading_date < day]
        reference = past_session_reference(
            day_close=to_price(own[-1].close) if own else None,
            day_change=to_price(own[-1].change) if own else None,
            prior_close=to_price(before[-1].close) if before else None,
        )
        if reference is not None:
            found[symbol] = reference
    return found


def account_start_date(account: PaperAccountRecord) -> date:
    return local(account.reset_at or account.created_at).date()


async def benchmark_closes(
    repo: PaperRepository, start: date, end: date | None = None
) -> dict[date, float]:
    """TAIEX closes (market TW) by trading date, newest revision per date."""
    observations = await repo.list_market_observations("TW", start=start, end=end)
    closes: dict[date, float] = {}
    for observation in observations:  # sorted by (date, revision): the last one wins
        closes[observation.trading_date] = observation.index_close
    return closes


def previous_snapshot(snapshots: list[SnapshotRecord], before: date) -> SnapshotRecord | None:
    older = [s for s in snapshots if s.trading_date < before]
    return older[-1] if older else None


def owned(account: PaperAccountRecord | None, user_id: UUID) -> bool:
    return account is not None and not account.archived and account.user_id == user_id
