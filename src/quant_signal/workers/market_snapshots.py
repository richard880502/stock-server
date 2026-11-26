"""Whole-market latest prices and the Shioaji traffic monitor (collector only).

* Every ``quotes_snapshot_minutes`` (default 10) during the stock session, one pass of
  Shioaji snapshots (500 contracts per query, ~4 queries for the whole market)
  refreshes ``quote_latest`` for every listed and OTC stock. Sector rotation and any
  page showing a stock nobody subscribed to read those rows.
* When a symbol is subscribed, a single-contract snapshot seeds its latest price at
  once, so a page opened after the last trade (or after the close) still shows
  today's price instead of yesterday's official close.
* ``UsageMonitor`` reads ``api.usage()`` (UsageStatus) at most once a minute, shares
  it in Redis for the health endpoint, and tells heavy jobs to stop when today's
  remaining traffic falls below the reserve.

Every snapshot query goes through the shared Redis budget like kbars do.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time as monotonic
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from quant_signal.application.quote_cache import PREFIX, is_futures
from quant_signal.infrastructure.db.database import session_factory
from quant_signal.infrastructure.db.prices import LatestQuote, store_latest
from quant_signal.infrastructure.providers.shioaji_budget import BudgetExhausted

log = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
USAGE_KEY = PREFIX + "usage"
SLOT_KEY = PREFIX + "snapshot:"
SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(13, 30)
# One extra pass after the closing auction so the day ends on the closing price.
CLOSING_PASS = time(13, 35)
BATCH = 500


def snapshot_slot(now: datetime, every_minutes: int) -> str | None:
    """The slot label (``YYYY-MM-DD HH:MM``) due at ``now``, or None outside the session."""
    if every_minutes <= 0:
        return None
    local = now.astimezone(TAIPEI)
    if local.weekday() >= 5:
        return None
    clock = local.time()
    if CLOSING_PASS <= clock < time(13, 50):
        return f"{local.date().isoformat()} {CLOSING_PASS.strftime('%H:%M')}"
    if not SESSION_OPEN <= clock <= SESSION_CLOSE:
        return None
    minutes = (clock.hour * 60 + clock.minute) // every_minutes * every_minutes
    return f"{local.date().isoformat()} {minutes // 60:02d}:{minutes % 60:02d}"


def quote_event(quote: LatestQuote, unit: str) -> dict:
    """A snapshot in the same shape as a live tick event (for the Redis cache)."""
    change_pct = quote.change_pct
    event = {
        "symbol": quote.symbol,
        "price": quote.price,
        "change_pct": change_pct if change_pct is not None else 0.0,
        "total_volume": quote.volume or 0,
        "volume": 0,
        "unit": unit,
        "trade_time": quote.trade_time.isoformat(),
        "received_at": datetime.now(UTC).isoformat(),
        "trade_epoch": quote.trade_time.timestamp(),
        "source": "snapshot",
    }
    for name in ("open", "high", "low"):
        value = getattr(quote, name)
        if value is not None:
            event[f"day_{name}"] = value
    if quote.reference is not None:
        event["reference"] = quote.reference
    return event


class UsageMonitor:
    """Caches ``api.usage()`` and decides whether heavy queries may continue."""

    def __init__(
        self,
        usage: Callable[[], dict | None],
        redis: Any,
        reserve_bytes: int,
        *,
        every_seconds: float = 60,
    ) -> None:
        self._usage = usage
        self._redis = redis
        self._reserve = reserve_bytes
        self._every = every_seconds
        self._checked = -1e9
        self.last: dict | None = None

    async def refresh(self, force: bool = False) -> dict | None:
        if not force and monotonic.monotonic() - self._checked < self._every:
            return self.last
        self._checked = monotonic.monotonic()
        usage = await asyncio.to_thread(self._usage)
        if usage is not None:
            self.last = usage
            try:
                await self._redis.set(
                    USAGE_KEY,
                    json.dumps({**usage, "checked_at": datetime.now(UTC).isoformat()}),
                    ex=86400,
                )
            except Exception:
                log.warning("Shioaji usage not shared")
        return self.last

    async def ok(self) -> bool:
        """True unless today's remaining traffic is known to be below the reserve."""
        usage = await self.refresh()
        return usage is None or usage["remaining_bytes"] >= self._reserve


class MarketSnapshots:
    """Schedules snapshot passes and single-symbol seeds inside the collector."""

    def __init__(
        self,
        redis: Any,
        settings: Any,
        fetch_quotes: Callable[[list[str]], Awaitable[list[LatestQuote]]],
        universe: Callable[[], Awaitable[list[str]]],
        usage: UsageMonitor,
        publish: Callable[[dict], Awaitable[None]],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(TAIPEI),
    ) -> None:
        self.redis = redis
        self.every = settings.quotes_snapshot_minutes
        self.fetch_quotes = fetch_quotes
        self.universe = universe
        self.usage = usage
        self.publish = publish
        self.now = now
        self._task: asyncio.Task | None = None
        self._seeded: dict[str, date] = {}

    async def tick(self) -> bool:
        """Start a whole-market pass when one is due and none is running. Never raises."""
        slot = snapshot_slot(self.now(), self.every)
        if slot is None or (self._task is not None and not self._task.done()):
            return False
        try:
            if not await self.redis.set(SLOT_KEY + slot, "1", nx=True, ex=86400):
                return False
        except Exception:
            log.warning("Snapshot pass not scheduled")
            return False
        self._task = asyncio.create_task(self._pass(slot))
        return True

    async def _pass(self, slot: str) -> int:
        stored = 0
        try:
            symbols = [s for s in await self.universe() if not is_futures(s)]
            for index in range(0, len(symbols), BATCH):
                if not await self.usage.ok():
                    log.info("Shioaji traffic reserve reached; snapshot pass stopped")
                    break
                quotes = await self.fetch_quotes(symbols[index : index + BATCH])
                async with session_factory() as session:
                    stored += await store_latest(session, quotes)
                    await session.commit()
            log.info("Snapshot pass %s: %d symbols", slot, stored)
        except BudgetExhausted:
            log.info("Shioaji query budget reached; snapshot pass %s stopped", slot)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Snapshot pass %s failed", slot)
        return stored

    async def seed(self, symbol: str) -> bool:
        """Latest price for a newly subscribed stock, once per symbol per day. Never raises."""
        today = self.now().astimezone(TAIPEI).date()
        if is_futures(symbol) or self._seeded.get(symbol) == today:
            return False
        self._seeded[symbol] = today
        try:
            if not await self.usage.ok():
                return False
            quotes = await self.fetch_quotes([symbol])
            if not quotes:
                return False
            async with session_factory() as session:
                await store_latest(session, quotes)
                await session.commit()
            for quote in quotes:
                await self.publish(quote_event(quote, "lot"))
            return True
        except BudgetExhausted:
            return False
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Snapshot seed failed")
            return False

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
