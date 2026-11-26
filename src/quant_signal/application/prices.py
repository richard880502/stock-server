"""The read side of prices: which source answers "what is the price now".

Three layers, one rule:

* official daily bars (TWSE / TPEx, synced after the close) are the record;
* ``quote_latest`` (live ticks for subscribed symbols, whole-market snapshots every
  10 minutes) is newer than the record during and right after the session;
* ``minute_bars`` hold the intraday path (live, then replaced by official kbars).

A latest quote is used only when it is from a later trading day than the newest
official bar; once the official day lands, the official close wins. Everything that
shows "today's price" (watchlist, terminal, sector rotation, the backtest's
provisional bar) goes through these helpers or the repository methods that use them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from quant_signal.infrastructure.db.prices import LatestQuote

TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class PriceNow:
    price: float
    change_pct: float | None  # percent (1.23 = +1.23%), like the official daily rows
    at: datetime
    source: str  # "tick" (live trade) or "snapshot" (whole-market pass)


def trading_day(moment: datetime) -> date:
    return moment.astimezone(TAIPEI).date()


def price_now(quote: LatestQuote | None, official_through: date | None) -> PriceNow | None:
    """The latest quote if it is newer than the official record, else None."""
    if quote is None:
        return None
    if official_through is not None and trading_day(quote.trade_time) <= official_through:
        return None
    change = quote.change_pct
    return PriceNow(
        price=quote.price,
        change_pct=None if change is None else round(change * 100, 4),
        at=quote.trade_time,
        source=quote.source,
    )
