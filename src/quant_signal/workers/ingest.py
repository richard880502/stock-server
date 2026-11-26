"""The ingest service: the only process that writes market data.

Three writers live here, deployed together from the same commit:

* the official exchange sync (TWSE / TPEx daily bars, institutional flows, the
  whole-market day, industry classification),
* the news (消息面) job: twice a day, headlines for watchlist + tracked symbols, and
* the Shioaji collector (live ticks -> minute bars and latest prices, official
  minute backfill, whole-market snapshots), when quotes are enabled.

Plus storage upkeep: monthly ``minute_bars`` partitions are created ahead of time
and, if a retention is configured, old months are dropped.

The API, MCP and the compute worker only read. Schema migrations run in the API
container before it starts; until they have run, this service retries quietly.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from quant_signal.infrastructure.db.database import session_factory
from quant_signal.infrastructure.db.prices import drop_expired_partitions, ensure_partitions
from quant_signal.settings import get_settings

log = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
UPKEEP_EVERY_SECONDS = 6 * 3600
UPKEEP_RETRY_SECONDS = 300


def quotes_ready(settings) -> bool:
    return bool(
        settings.quotes_enabled
        and settings.shioaji_api_key.get_secret_value()
        and settings.shioaji_secret_key.get_secret_value()
    )


async def upkeep_once(settings) -> list[str]:
    today = datetime.now(TAIPEI).date()
    async with session_factory() as session:
        await ensure_partitions(session, today)
        dropped = await drop_expired_partitions(
            session, today, settings.minute_bars_retention_months
        )
        await session.commit()
    return dropped


async def upkeep_forever() -> None:
    settings = get_settings()
    while True:
        try:
            dropped = await upkeep_once(settings)
            if dropped:
                log.info("Dropped minute_bars partitions past retention: %s", ", ".join(dropped))
            await asyncio.sleep(UPKEEP_EVERY_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Usually: migrations not applied yet. Try again soon.
            log.warning("minute_bars partition upkeep failed; retrying")
            await asyncio.sleep(UPKEEP_RETRY_SECONDS)


async def liveness() -> None:
    """Health endpoint for the container when the collector is off (it serves its own)."""
    from quant_signal.workers.quote_health import QuoteHealth, serve

    health = QuoteHealth()

    async def pulse() -> None:
        while True:
            health.alive = time.monotonic()
            await asyncio.sleep(5)

    await asyncio.gather(serve(health, None), pulse())


async def run_forever() -> None:
    from quant_signal.workers import news as news_worker
    from quant_signal.workers import quotes
    from quant_signal.workers import sync as sync_worker

    settings = get_settings()
    jobs = [asyncio.create_task(upkeep_forever(), name="upkeep")]
    if settings.sync_enabled:
        jobs.append(asyncio.create_task(sync_worker.run_forever(), name="official-sync"))
    if settings.news_sync_enabled:
        # Own task: a slow or failing news fetch can never stall prices.
        jobs.append(asyncio.create_task(news_worker.run_forever(), name="news"))
    if quotes_ready(settings):
        jobs.append(asyncio.create_task(quotes.run(), name="shioaji-collector"))
    else:
        log.info("Shioaji collector off (QUOTES_ENABLED or credentials missing)")
        jobs.append(asyncio.create_task(liveness(), name="liveness"))
    try:
        # The collector returns on SIGTERM; any job ending means the service should stop
        # (the container restarts it if it crashed).
        done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.exception() is not None:
                log.error("ingest job %s stopped", task.get_name(), exc_info=task.exception())
    finally:
        for task in jobs:
            task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)


def main() -> None:
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
