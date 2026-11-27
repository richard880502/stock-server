from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta

from quant_signal.application.derivatives_data import refresh_derivatives_data
from quant_signal.application.holders_data import refresh_holders_data
from quant_signal.application.market_industry import refresh_industries
from quant_signal.application.official_events import refresh_official_data
from quant_signal.application.ports import QuantRepository
from quant_signal.application.screener import refresh_screener
from quant_signal.application.sync_jobs import (
    enqueue_daily_refresh,
    ensure_market_snapshots,
    process_next_job,
    retry_delay_seconds,
)
from quant_signal.infrastructure.db import PostgresQuantRepository, session_factory
from quant_signal.infrastructure.providers.taifex import TaifexClient
from quant_signal.infrastructure.providers.tpex import TpexProvider
from quant_signal.infrastructure.providers.twse import TwseProvider
from quant_signal.settings import get_settings

logger = logging.getLogger(__name__)

# A failed refresh is retried after an hour, not on every poll.
INDUSTRY_RETRY_AFTER = timedelta(hours=1)
_industry_attempt: dict[str, datetime] = {"at": datetime.min.replace(tzinfo=UTC)}
# Dataset -> last failed attempt for the official events refresh (see application/official_events).
_official_attempts: dict[str, datetime] = {}
# Upkeep catch-up of the factor snapshot is checked at most this often.
SCREENER_UPKEEP_EVERY = timedelta(minutes=10)
_screener_attempt: dict[str, datetime] = {"at": datetime.min.replace(tzinfo=UTC)}
# Dates already computed for their current source rows, so upkeep does not redo them.
_screener_settled: dict[date, tuple[tuple, bool]] = {}


async def _refresh_screener(repository: QuantRepository, *, recompute_latest: bool) -> None:
    settings = get_settings()
    done = await refresh_screener(
        repository,
        enabled=settings.screener_enabled,
        retention_sessions=settings.screener_retention_sessions,
        backfill_sessions=settings.screener_backfill_sessions,
        recompute_latest=recompute_latest,
        settled=_screener_settled,
    )
    if done:
        logger.info("screener snapshot computed for %s", ", ".join(d.isoformat() for d in done))


async def _after_market_sync(repository: QuantRepository) -> None:
    # Flow and credit reports arrive after the quotes, so every market sync rebuilds the
    # newest date.
    await _refresh_screener(repository, recompute_latest=True)


_holders_attempts: dict[str, datetime] = {}
_derivatives_attempts: dict[str, datetime] = {}
_derivatives_specs: dict[str, object] = {}


async def tick(
    twse: TwseProvider, tpex: TpexProvider, taifex: TaifexClient | None = None
) -> bool | None:
    """One scheduling pass: recover stuck jobs, queue the daily refresh, run one job."""
    settings = get_settings()
    async with session_factory() as session:
        repository = PostgresQuantRepository(session)
        recovered = await repository.requeue_stale_sync_jobs(
            older_than_seconds=settings.sync_stale_after_seconds
        )
        if recovered:
            logger.warning("requeued %s sync jobs stuck in running", recovered)
        await ensure_market_snapshots(repository, lookback_days=settings.sync_market_lookback_days)
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
        if settings.holders_tdcc_enabled or settings.holders_foreign_enabled:
            try:
                await refresh_holders_data(
                    repository,
                    twse,
                    tpex,
                    now=now,
                    attempts=_holders_attempts,
                    tdcc_enabled=settings.holders_tdcc_enabled,
                    foreign_enabled=settings.holders_foreign_enabled,
                    foreign_window=settings.holders_foreign_days,
                    retry_after=timedelta(minutes=settings.holders_retry_minutes),
                )
            except Exception:
                # Holder data is a side feed; never let it stall the price sync.
                logger.exception("holders data refresh failed")
        if settings.derivatives_enabled and taifex is not None:
            try:
                await refresh_derivatives_data(
                    repository,
                    taifex,
                    now=now,
                    attempts=_derivatives_attempts,
                    specs_cache=_derivatives_specs,
                    backfill_sessions=settings.derivatives_backfill_sessions,
                    after_close=settings.derivatives_after_close,
                    request_budget=settings.derivatives_requests_per_tick,
                    retry_after=timedelta(minutes=settings.derivatives_retry_minutes),
                )
            except Exception:
                # Derivatives are a side feed; never let them stall the price sync.
                logger.exception("derivatives data refresh failed")
        await enqueue_daily_refresh(
            repository,
            now=datetime.now(UTC),
            daily_time=settings.sync_daily_time,
            lookback_days=settings.sync_lookback_days,
            market_lookback_days=settings.sync_market_lookback_days,
        )
        outcome = await process_next_job(
            repository,
            twse,
            tpex,
            max_attempts=settings.sync_max_attempts,
            after_market_sync=_after_market_sync,
        )
        if outcome is None and now - _screener_attempt["at"] >= SCREENER_UPKEEP_EVERY:
            # Idle: catch up on any date the snapshot is missing (first run, downtime).
            _screener_attempt["at"] = now
            await _refresh_screener(repository, recompute_latest=False)
        return outcome


async def run_forever() -> None:
    """Drain the sync queue through one shared, throttled provider pair.

    Run a single instance: the per-request interval lives in the providers, so
    extra replicas would multiply the load on the exchanges.
    """
    settings = get_settings()
    failures = 0
    taifex_client = TaifexClient(
        request_interval_seconds=settings.derivatives_request_interval_seconds
    )
    async with TwseProvider() as twse, TpexProvider() as tpex, taifex_client as taifex:
        while True:
            try:
                outcome = await tick(twse, tpex, taifex)
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
