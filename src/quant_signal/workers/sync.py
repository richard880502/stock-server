from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from quant_signal.application.market_industry import refresh_industries
from quant_signal.application.official_events import refresh_official_data
from quant_signal.application.sync_jobs import (
    enqueue_daily_refresh,
    ensure_market_snapshots,
    process_next_job,
    retry_delay_seconds,
)
from quant_signal.infrastructure.db import PostgresQuantRepository, session_factory
from quant_signal.infrastructure.providers.tpex import TpexProvider
from quant_signal.infrastructure.providers.twse import TwseProvider
from quant_signal.settings import get_settings

logger = logging.getLogger(__name__)

# A failed refresh is retried after an hour, not on every poll.
INDUSTRY_RETRY_AFTER = timedelta(hours=1)
_industry_attempt: dict[str, datetime] = {"at": datetime.min.replace(tzinfo=UTC)}
# Dataset -> last failed attempt for the official events refresh (see application/official_events).
_official_attempts: dict[str, datetime] = {}


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
        await ensure_market_snapshots(
            repository, lookback_days=settings.sync_market_lookback_days
        )
        now = datetime.now(UTC)
        if now - _industry_attempt["at"] >= INDUSTRY_RETRY_AFTER:
            _industry_attempt["at"] = now
            try:
                await refresh_industries(repository, twse, tpex, now=now)
            except Exception:
                # Classification is a weekly nicety; never let it stall the price sync.
                logger.exception("industry classification refresh failed")
        if settings.official_events_enabled:
            try:
                await refresh_official_data(
                    repository,
                    twse,
                    tpex,
                    now=now,
                    attempts=_official_attempts,
                    material_minutes=settings.official_events_material_minutes,
                    retry_after=timedelta(minutes=settings.official_events_retry_minutes),
                )
            except Exception:
                # Events are a side feed; never let them stall the price sync.
                logger.exception("official events refresh failed")
        await enqueue_daily_refresh(
            repository,
            now=datetime.now(UTC),
            daily_time=settings.sync_daily_time,
            lookback_days=settings.sync_lookback_days,
            market_lookback_days=settings.sync_market_lookback_days,
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
