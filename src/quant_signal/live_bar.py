"""Provisional intraday daily bar built from collector minute rows.

Pure and I/O free. Only the quote collector talks to Shioaji; this just folds the
``quote_minutes`` rows it stored. Point in time: minutes whose last trade is after
``as_of`` are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from quant_signal.domain.models import Bar

TAIPEI = ZoneInfo("Asia/Taipei")
PROVISIONAL_SOURCE = "live_provisional"


@dataclass(frozen=True)
class MinuteQuote:
    minute: datetime
    last_time: datetime
    open: float
    high: float
    low: float
    close: float
    first_total_volume: int  # lots
    last_total_volume: int  # lots


def build_provisional_bar(
    symbol: str,
    trading_date: date,
    minutes: list[MinuteQuote],
    *,
    as_of: datetime,
    adjustment_factor: float = 1.0,
) -> tuple[Bar, datetime] | None:
    """Return (bar, last_minute_time) or None when no usable minute exists.

    open = first minute open, high/low = extremes, close = last minute close,
    volume = sum of per-minute (last_total_volume - first_total_volume) lots x 1000
    shares. Rows with non-positive prices are dropped. If the collector started
    mid-session the open/volume only cover what was captured.
    """
    usable = sorted(
        (
            m
            for m in minutes
            if m.last_time <= as_of
            and m.minute.astimezone(TAIPEI).date() == trading_date
            and min(m.open, m.high, m.low, m.close) > 0
        ),
        key=lambda m: m.minute,
    )
    if not usable:
        return None
    last = usable[-1]
    high = max(m.high for m in usable)
    low = min(m.low for m in usable)
    opening = usable[0].open
    closing = last.close
    bar = Bar(
        symbol=symbol,
        trading_date=trading_date,
        open=opening,
        high=max(high, opening, closing),
        low=min(low, opening, closing),
        close=closing,
        adjustment_factor=adjustment_factor,
        volume=sum(max(0, m.last_total_volume - m.first_total_volume) for m in usable) * 1000,
        available_at=last.last_time,
        source=PROVISIONAL_SOURCE,
    )
    return bar, last.last_time
