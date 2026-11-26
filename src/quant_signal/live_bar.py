"""Provisional daily bar for today, folded from the collector's 1-minute bars.

Pure and I/O free: only the ingest service talks to Shioaji; this just folds the
``minute_bars`` it stored. Point in time: only whole minutes that ended at or before
``as_of`` are used.

Adjustment: minute prices are raw. History is ``raw x adjustment_factor`` with a
factor that keeps prices continuous across ex-rights / ex-dividend dates. The
exchange's reference price for today (平盤價) already reflects any ex-date, so the
provisional bar's factor is ``previous factor x previous raw close / reference``:
equal to the previous factor on an ordinary day, and continuous on an ex-date.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from quant_signal.domain.models import Bar
from quant_signal.infrastructure.db.prices import MinuteBar

TAIPEI = ZoneInfo("Asia/Taipei")
PROVISIONAL_SOURCE = "live_provisional"
MINUTE = timedelta(minutes=1)
LIMIT_PCT = 0.10
EX_RIGHTS_NOTE = "今日可能為除權息日，但沒有交易所參考價可校正還原權值，暫不加入盤中暫定 K 棒。"


def provisional_factor(
    previous_factor: float, previous_close: float, reference: float | None
) -> float | None:
    """Adjustment factor for today's raw prices, or None when it cannot be trusted.

    With the exchange reference price the factor is exact. Without it, an ordinary
    day is assumed (factor unchanged) unless the caller's guard rejects the bar.
    """
    if previous_close <= 0:
        return None
    if reference is None or reference <= 0:
        return previous_factor
    return previous_factor * previous_close / reference


def build_provisional_bar(
    symbol: str,
    trading_date: date,
    minutes: list[MinuteBar],
    *,
    as_of: datetime,
    adjustment_factor: float = 1.0,
) -> tuple[Bar, datetime] | None:
    """Return (bar, end of last minute used) or None when no usable minute exists.

    open = first minute open, high/low = extremes, close = last minute close,
    volume = sum of minute volumes (lots) x 1000 shares. If the feed started
    mid-session and the official backfill hasn't run yet, open/volume only cover
    what was captured.
    """
    usable = sorted(
        (
            m
            for m in minutes
            if m.minute + MINUTE <= as_of
            and m.minute.astimezone(TAIPEI).date() == trading_date
            and min(m.open, m.high, m.low, m.close) > 0
        ),
        key=lambda m: m.minute,
    )
    if not usable:
        return None
    last = usable[-1]
    ended = last.minute + MINUTE
    opening, closing = usable[0].open, last.close
    bar = Bar(
        symbol=symbol,
        trading_date=trading_date,
        open=opening,
        high=max(max(m.high for m in usable), opening, closing),
        low=min(min(m.low for m in usable), opening, closing),
        close=closing,
        adjustment_factor=adjustment_factor,
        volume=sum(max(0, m.volume) for m in usable) * 1000,
        available_at=ended,
        source=PROVISIONAL_SOURCE,
    )
    return bar, ended


def ex_rights_suspected(bar: Bar, last_official_close: float) -> bool:
    """Fallback guard when no reference price is known.

    A raw open beyond the +-10% daily limit from yesterday's raw close is impossible
    on an ordinary day, so it implies an ex-date we cannot correct for.
    """
    return abs(bar.open / last_official_close - 1) > LIMIT_PCT + 1e-6
