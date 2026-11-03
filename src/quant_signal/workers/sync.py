from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from quant_signal.application.sync_jobs import (
    enqueue_daily_refresh,
    process_next_job,
    retry_delay_seconds,
)
from quant_signal.infrastructure.db import PostgresQuantRepository, session_factory
from quant_signal.infrastructure.providers.tpex import TpexProvider
from quant_signal.infrastructure.providers.twse import TwseProvider
from quant_signal.settings import get_settings

logger = logging.getLogger(__name__)


async def tick(twse: TwseProvider, tpex: TpexProvider) -> bool | None:
    """One scheduling pass: recover stuck jobs, queue the daily refresh, run one job."""
    settings = get_settings()
    async with session_factory() as session:
        repository = PostgresQuantRepository(session)
        recovered = await repository.requeue_stale_sync_jobs(
            older_than_seconds=settings.sync_stale_after_seconds
        )
        if recovered:
            logger.warning("requeued %s sync jobs stuck in running", recovered)
        await enqueue_daily_refresh(
            repository,
            now=datetime.now(UTC),
            daily_time=settings.sync_daily_time,
            lookback_days=settings.sync_lookback_days,
        )
        return await process_next_job(
            repository,
            twse,
            tpex,
            max_attempts=settings.sync_max_attempts,
        )


async def run_forever() -> None:
    """Drain the sync queue through one shared, throttled provider pair.

    Run a single instance: the per-request interval lives in the providers, so
    extra replicas would multiply the load on the exchanges.
    """
    settings = get_settings()
    failures = 0
    async with TwseProvider() as twse, TpexProvider() as tpex:
        while True:
            try:
                outcome = await tick(twse, tpex)
            except Exception:
                logger.exception("sync worker tick failed")
                outcome = False
            if outcome is None:
                failures = 0
                await asyncio.sleep(settings.sync_poll_seconds)
            elif outcome:
                failures = 0
            else:
                failures += 1
                await asyncio.sleep(retry_delay_seconds(settings.sync_poll_seconds, failures))
