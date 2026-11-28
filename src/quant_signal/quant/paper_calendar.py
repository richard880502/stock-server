"""Taiwan trading sessions for paper trading (pure; uses the XTAI exchange calendar).

Continuous trading runs 09:00-13:25 and the closing auction result prints at 13:30.
Dates beyond the bundled calendar fall back to "weekdays are trading days".
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

TAIPEI = ZoneInfo("Asia/Taipei")
SESSION_OPEN = time(9, 0)
CONTINUOUS_END = time(13, 25)
SESSION_CLOSE = time(13, 30)
# The closing-auction print carries a trade time of 13:30 but reaches us a moment later,
# so observations (and the live matcher) are accepted until this time. Order validity
# still follows SESSION_CLOSE: an order created after 13:30:00 belongs to the next session.
SESSION_ACCEPT_UNTIL = time(13, 35)
MAX_LOOKAHEAD_DAYS = 20


@lru_cache(maxsize=1)
def _calendar():
    return xcals.get_calendar("XTAI")


@lru_cache(maxsize=4096)
def is_trading_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    try:
        return bool(_calendar().is_session(pd.Timestamp(day)))
    except Exception:  # outside the calendar's range
        return True


def next_trading_day(day: date) -> date:
    """The first trading day strictly after ``day``."""
    current = day
    for _ in range(MAX_LOOKAHEAD_DAYS):
        current += timedelta(days=1)
        if is_trading_day(current):
            return current
    return current


def previous_trading_day(day: date) -> date:
    """The last trading day strictly before ``day``."""
    current = day
    for _ in range(MAX_LOOKAHEAD_DAYS):
        current -= timedelta(days=1)
        if is_trading_day(current):
            return current
    return current


def local(moment: datetime) -> datetime:
    return moment.astimezone(TAIPEI)


def local_date(moment: datetime) -> date:
    return local(moment).date()


def session_window(day: date) -> tuple[datetime, datetime]:
    """(first, last) instant a trade on ``day`` can carry, Taipei time."""
    return (
        datetime.combine(day, SESSION_OPEN, TAIPEI),
        datetime.combine(day, SESSION_CLOSE, TAIPEI),
    )


def observation_window(day: date) -> tuple[datetime, datetime]:
    """(first, last) instant an observation may carry to count for an order of ``day``."""
    return (
        datetime.combine(day, SESSION_OPEN, TAIPEI),
        datetime.combine(day, SESSION_ACCEPT_UNTIL, TAIPEI),
    )


def matcher_open(now: datetime) -> bool:
    """True while the live matcher should run: the session plus the closing-print grace."""
    moment = local(now)
    return is_trading_day(moment.date()) and SESSION_OPEN <= moment.time() <= SESSION_ACCEPT_UNTIL


def in_session(now: datetime) -> bool:
    """True while trades can print: a trading day, 09:00 through the 13:30 close."""
    moment = local(now)
    return is_trading_day(moment.date()) and SESSION_OPEN <= moment.time() <= SESSION_CLOSE


def session_for_order(created_at: datetime) -> tuple[date, bool]:
    """(session_date, queued_for_next_session) for an order created at ``created_at``.

    Before the close on a trading day (pre-open included) the order is for today.
    From 13:30, or on a non-trading day, it waits for the next trading day's first trade.
    """
    moment = local(created_at)
    day = moment.date()
    if is_trading_day(day) and moment.time() < SESSION_CLOSE:
        return day, False
    return next_trading_day(day), True


def latest_session_day(now: datetime) -> date:
    """The trading day whose prices are "current": today once 09:00 has passed."""
    moment = local(now)
    day = moment.date()
    if is_trading_day(day) and moment.time() >= SESSION_OPEN:
        return day
    return previous_trading_day(day)


def utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)
