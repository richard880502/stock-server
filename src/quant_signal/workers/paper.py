"""The ``paper-matcher`` task of the ingest service (paper trading, simulation only).

Every ``PAPER_MATCH_SECONDS`` it matches working paper orders against prices we already
store, expires what never traded after the close, writes the daily equity snapshots and
keeps demand registered for the symbols paper trading cares about. It never talks to a
broker and never imports the Shioaji SDK: the only thing it asks of the collector is
"please subscribe this symbol", through the same capped Redis demand set the API uses.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from redis.asyncio import Redis

from quant_signal.application.paper_jobs import PaperJobs
from quant_signal.application.quote_cache import ADMIT, DEMAND
from quant_signal.infrastructure.db.database import session_factory
from quant_signal.quant.paper_calendar import in_session

log = logging.getLogger(__name__)
IDLE_SECONDS = 20.0  # outside the session there is nothing to match every 3 seconds
FAILURE_BACKOFF_SECONDS = 30.0


class RedisDemand:
    """Admit one symbol into the shared demand set, under the collector's global cap."""

    def __init__(self, redis: Redis, max_symbols: int) -> None:
        self.redis = redis
        self.max_symbols = max_symbols

    async def admit(self, symbol: str) -> bool:
        return bool(await self.redis.eval(ADMIT, 1, DEMAND, time.time(), self.max_symbols, symbol))


async def run_forever(settings) -> None:
    from quant_signal.infrastructure.db import PostgresQuantRepository

    redis = None
    demand = None
    if settings.quotes_enabled:
        redis = Redis.from_url(
            settings.quotes_redis_url.get_secret_value(),
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        demand = RedisDemand(redis, settings.quotes_max_symbols)
    jobs = PaperJobs(slippage_ticks=settings.paper_slippage_ticks, demand=demand)
    try:
        while True:
            now = datetime.now(UTC)
            try:
                async with session_factory() as session:
                    await jobs.tick(PostgresQuantRepository(session), now)
                pause = settings.paper_match_seconds if in_session(now) else IDLE_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception:
                # Usually migrations not applied yet, or Redis hiccups. Never stop the service.
                log.warning("Paper matcher pass failed; retrying", exc_info=True)
                pause = FAILURE_BACKOFF_SECONDS
            await asyncio.sleep(pause)
    finally:
        if redis is not None:
            await redis.aclose()
