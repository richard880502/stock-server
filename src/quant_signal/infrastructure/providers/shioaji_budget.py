"""Shared budget for Shioaji history queries (kbars, ticks, snapshots).

Shioaji's published limits (https://sinotrade.github.io/zh/tutor/limit/):

* snapshots, ticks, kbars, credit and short queries share 50 calls per 10 seconds;
* during the trading session at most 10 ticks and 270 kbars queries;
* going over suspends the service for a minute and repeated abuse can ban the IP/ID.

The budget lives in Redis so the collector, a restarted collector and the manual
backfill CLI all draw from the same allowance. It fails closed: if Redis cannot be
reached or the allowance is used up, the query is not sent.

"During the session" is not defined by Shioaji. To stay safe it is read broadly here:
weekdays 08:00-14:30 (stocks 09:00-13:30, futures day session 08:45-13:45) plus the
futures night session 15:00-05:00, which counts toward the day it started on.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time as monotonic_clock
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

TAIPEI = ZoneInfo("Asia/Taipei")
PREFIX = "quant:quotes:budget:"
WINDOW_KEY = PREFIX + "window"
# 50 per 10 s is Shioaji's shared limit; keep 10 in reserve for clock skew and for
# anything else using the same account.
WINDOW_LIMIT = 40
WINDOW_SECONDS = 10.0
# Shioaji allows 10 ticks queries during the session.
TICKS_SESSION_CAP = 8
DAY_TTL_SECONDS = 2 * 86400

Kind = Literal["kbars", "ticks", "snapshots"]
KINDS: tuple[str, ...] = ("kbars", "ticks", "snapshots")


class BudgetExhausted(Exception):
    """The query was not sent: the budget is used up or Redis is unavailable."""


def session_day(moment: datetime) -> date | None:
    """The trading day whose session-limited allowance ``moment`` draws from, if any."""
    local = moment.astimezone(TAIPEI)
    clock = local.time()
    if clock < time(5, 0):
        day = local.date() - timedelta(days=1)  # Night session carries over midnight.
    elif time(8, 0) <= clock < time(14, 30) or clock >= time(15, 0):
        day = local.date()
    else:
        return None
    return day if day.weekday() < 5 else None


class ShioajiBudget:
    def __init__(
        self,
        redis: Any,
        *,
        kbars_session_cap: int,
        ticks_session_cap: int = TICKS_SESSION_CAP,
        window_limit: int = WINDOW_LIMIT,
        window_seconds: float = WINDOW_SECONDS,
        max_wait: float = 20.0,
    ) -> None:
        if window_limit > 45 or kbars_session_cap > 260 or ticks_session_cap > 10:
            raise ValueError("budget must stay below Shioaji's published limits")
        self.redis = redis
        self.session_caps = {"kbars": kbars_session_cap, "ticks": ticks_session_cap}
        self.window_limit = window_limit
        self.window_seconds = window_seconds
        self.max_wait = max_wait

    @classmethod
    def from_settings(cls, redis: Any, settings: Any) -> ShioajiBudget:
        return cls(redis, kbars_session_cap=settings.quotes_backfill_intraday_max)

    async def acquire(self, kind: Kind) -> None:
        """Wait for a slot in the rolling window; raise BudgetExhausted instead of sending."""
        if kind not in KINDS:
            raise ValueError("unknown Shioaji query kind")
        deadline = monotonic_clock.monotonic() + self.max_wait
        try:
            while True:
                wait = await self._try(kind)
                if wait is None:
                    return
                if monotonic_clock.monotonic() + wait > deadline:
                    raise BudgetExhausted("Shioaji query window is busy")
                await asyncio.sleep(min(max(wait, 0.05), 1.0))
        except BudgetExhausted:
            raise
        except Exception:
            # Fail closed. Never query Shioaji without being able to count it.
            log.warning("Shioaji query budget unavailable; query skipped")
            raise BudgetExhausted("Shioaji query budget unavailable") from None

    async def _try(self, kind: str) -> float | None:
        """None when granted, else seconds until the window may have room."""
        seconds, micros = await self.redis.time()
        now = int(seconds) + int(micros) / 1e6
        day = session_day(datetime.fromtimestamp(now, TAIPEI))
        cap = self.session_caps.get(kind) if day else None
        day_key = f"{PREFIX}{kind}:{day.isoformat()}" if day else None
        if cap is not None and int(await self.redis.get(day_key) or 0) >= cap:
            raise BudgetExhausted(f"Shioaji {kind} session budget used up")
        member = f"{now:.6f}:{uuid4().hex}"
        pipeline = self.redis.pipeline(transaction=True)
        pipeline.zremrangebyscore(WINDOW_KEY, "-inf", now - self.window_seconds)
        pipeline.zadd(WINDOW_KEY, {member: now})
        pipeline.zcard(WINDOW_KEY)
        pipeline.expire(WINDOW_KEY, math.ceil(self.window_seconds) + 1)
        _, _, count, _ = await pipeline.execute()
        if count > self.window_limit:
            # Concurrent callers may both back off; neither ever over-grants.
            await self.redis.zrem(WINDOW_KEY, member)
            oldest = await self.redis.zrange(WINDOW_KEY, 0, 0, withscores=True)
            return (oldest[0][1] + self.window_seconds - now) if oldest else 0.1
        if cap is not None:
            used = await self.redis.incr(day_key)
            await self.redis.expire(day_key, DAY_TTL_SECONDS)
            if used > cap:
                await self.redis.zrem(WINDOW_KEY, member)
                raise BudgetExhausted(f"Shioaji {kind} session budget used up")
        return None

    async def used(self, kind: Kind, day: date) -> int:
        return int(await self.redis.get(f"{PREFIX}{kind}:{day.isoformat()}") or 0)
