"""Executes queued upstream syncs and schedules the daily refresh.

The sync worker is the only component that calls the exchanges on its own
initiative. Requests never fetch: they enqueue a deduplicated job here, and a
single worker drains the queue through long-lived providers so the request
throttle is shared by every job.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from quant_signal.application.data_sync import (
    TAIPEI,
    DataSyncReport,
    TpexDataSyncService,
    TwseDataSyncService,
    _is_tpex_symbol,
)
from quant_signal.application.ports import QuantRepository
from quant_signal.domain.models import FetchCoverage, SyncJob, SyncJobKind
from quant_signal.infrastructure.providers.tpex import TpexProvider
from quant_signal.infrastructure.providers.twse import TwseProvider

logger = logging.getLogger(__name__)

SCHEDULER_SOURCE = "scheduler"
SCHEDULER_DATASET = "daily_enqueue"
# Flow requests are per day, so a first-time backfill is capped well below the
# price lookback; later runs only fetch the days not yet recorded.
INSTITUTION_LOOKBACK_DAYS = 120


async def run_sync_job(
    job: SyncJob,
    repository: QuantRepository,
    twse: TwseProvider,
    tpex: TpexProvider,
    *,
    today: date | None = None,
) -> DataSyncReport:
    end = today or datetime.now(UTC).astimezone(TAIPEI).date()
    start = end - timedelta(days=job.lookback_days)
    service: TwseDataSyncService = (
        TpexDataSyncService(repository, tpex)  # type: ignore[arg-type]
        if _is_tpex_symbol(job.symbol)
        else TwseDataSyncService(repository, twse)  # type: ignore[arg-type]
    )
    if job.kind is SyncJobKind.INSTITUTIONS:
        return await service.sync_institutional_flows(job.symbol, start=start, end=end)
    return await service.sync_symbol(job.symbol, start=start, end=end)


async def process_next_job(
    repository: QuantRepository,
    twse: TwseProvider,
    tpex: TpexProvider,
    *,
    max_attempts: int,
    today: date | None = None,
) -> bool | None:
    """Run one queued job. ``None``: queue empty; ``True``/``False``: ok/failed."""
    job = await repository.claim_next_sync_job()
    if job is None:
        return None
    try:
        report = await run_sync_job(job, repository, twse, tpex, today=today)
    except Exception as exc:
        retry = job.attempts < max_attempts
        logger.exception(
            "sync job %s (%s %s) failed, attempt %s/%s",
            job.id,
            job.kind.value,
            job.symbol,
            job.attempts,
            max_attempts,
        )
        await repository.fail_sync_job(job.id, str(exc), retry=retry)
        return False
    await repository.complete_sync_job(job.id)
    logger.info(
        "sync job %s (%s %s) done: %s fetched, %s skipped, %s rows",
        job.id,
        job.kind.value,
        job.symbol,
        report.periods_fetched,
        report.periods_skipped,
        report.bars_inserted + report.institutional_flows_inserted,
    )
    return True


async def enqueue_daily_refresh(
    repository: QuantRepository,
    *,
    now: datetime,
    daily_time: str,
    lookback_days: int,
) -> int:
    """Queue one incremental refresh per tracked symbol, once per trading day.

    Runs after the configured Taipei time on weekdays. Returns the number of
    jobs created; ``0`` when it is too early, a weekend, or already done today.
    """
    local = now.astimezone(TAIPEI)
    if local.weekday() >= 5:
        return 0
    hour, minute = (int(part) for part in daily_time.split(":"))
    if (local.hour, local.minute) < (hour, minute):
        return 0
    period = local.date().isoformat()
    done = await repository.list_fetch_coverage(
        SCHEDULER_SOURCE, SCHEDULER_DATASET, "*", [period]
    )
    if period in done:
        return 0
    created = 0
    for symbol in await repository.list_tracked_symbols():
        _, was_created = await repository.enqueue_sync_job(
            SyncJobKind.SYMBOL, symbol, lookback_days=lookback_days
        )
        created += was_created
        if not symbol.startswith("^"):
            _, was_created = await repository.enqueue_sync_job(
                SyncJobKind.INSTITUTIONS, symbol, lookback_days=INSTITUTION_LOOKBACK_DAYS
            )
            created += was_created
    await repository.record_fetch_coverage(
        [
            FetchCoverage(
                source=SCHEDULER_SOURCE,
                dataset=SCHEDULER_DATASET,
                symbol="*",
                period=period,
                status="ok",
                row_count=created,
                fetched_at=now,
            )
        ]
    )
    logger.info("daily refresh queued %s sync jobs for %s", created, period)
    return created


def retry_delay_seconds(base: float, attempts: int, *, cap: float = 120.0) -> float:
    return min(cap, base * (2 ** max(attempts, 0)))


__all__ = [
    "INSTITUTION_LOOKBACK_DAYS",
    "enqueue_daily_refresh",
    "process_next_job",
    "retry_delay_seconds",
    "run_sync_job",
]
