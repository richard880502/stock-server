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
from quant_signal.application.market_observations import MarketObservationSyncService
from quant_signal.application.market_sync import MarketSnapshotSyncService
from quant_signal.application.ports import QuantRepository
from quant_signal.domain.models import FetchCoverage, SyncJob, SyncJobKind
from quant_signal.infrastructure.providers.tpex import TPEX_SOURCE, TpexProvider
from quant_signal.infrastructure.providers.twse import TWSE_SOURCE, TwseProvider

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
    if job.kind is SyncJobKind.MARKET:
        is_otc = job.symbol == "TWO"
        snapshot = MarketSnapshotSyncService(
            repository,  # type: ignore[arg-type]
            tpex if is_otc else twse,
            market="TWO" if is_otc else "TW",
            source=TPEX_SOURCE if is_otc else TWSE_SOURCE,
        )
        report = await snapshot.sync(start=start, end=end)
        # The environment score is derived from what was just stored.
        observations = await MarketObservationSyncService(
            repository,  # type: ignore[arg-type]
            tpex if is_otc else twse,  # type: ignore[arg-type]
            market="TWO" if is_otc else "TW",
            source=TPEX_SOURCE if is_otc else TWSE_SOURCE,
        ).sync(end=end)
        return report.model_copy(
            update={
                "market_observations_received": observations.market_observations_received,
                "market_observations_inserted": report.market_observations_inserted
                + observations.market_observations_inserted,
                "incomplete_capital_dates": observations.incomplete_capital_dates,
                "notes": [*report.notes, *observations.notes],
            }
        )
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
        report.bars_inserted
        + report.institutional_flows_inserted
        + report.market_observations_inserted,
    )
    return True


MARKET_CODES = ("TW", "TWO")


async def enqueue_market_snapshots(
    repository: QuantRepository, *, lookback_days: int
) -> int:
    created = 0
    for market in MARKET_CODES:
        _, was_created = await repository.enqueue_sync_job(
            SyncJobKind.MARKET, market, lookback_days=lookback_days
        )
        created += was_created
    return created


async def ensure_market_snapshots(
    repository: QuantRepository, *, lookback_days: int
) -> int:
    """Queue the first backfill when no whole-market data exists yet."""
    if await repository.list_market_trading_dates(None, as_of=None, limit=1):
        return 0
    return await enqueue_market_snapshots(repository, lookback_days=lookback_days)


async def enqueue_daily_refresh(
    repository: QuantRepository,
    *,
    now: datetime,
    daily_time: str,
    lookback_days: int,
    market_lookback_days: int = 520,
) -> int:
    """Queue incremental refreshes for tracked symbols and both markets.

    ``daily_time`` is one or more Taipei times (``"15:30,21:45"``). Each time
    triggers at most one refresh per weekday once it has passed. Returns the
    number of jobs created; ``0`` when nothing was due.
    """
    local = now.astimezone(TAIPEI)
    if local.weekday() >= 5:
        return 0
    due: list[str] = []
    for stamp in (part.strip() for part in daily_time.split(",") if part.strip()):
        hour, minute = (int(part) for part in stamp.split(":"))
        if (local.hour, local.minute) >= (hour, minute):
            due.append(f"{local.date().isoformat()}@{stamp}")
    if not due:
        return 0
    done = await repository.list_fetch_coverage(
        SCHEDULER_SOURCE, SCHEDULER_DATASET, "*", due
    )
    todo = [period for period in due if period not in done]
    if not todo:
        return 0
    created = await enqueue_market_snapshots(repository, lookback_days=market_lookback_days)
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
            for period in todo
        ]
    )
    logger.info("daily refresh queued %s sync jobs (%s)", created, ", ".join(todo))
    return created


def retry_delay_seconds(base: float, attempts: int, *, cap: float = 120.0) -> float:
    return min(cap, base * (2 ** max(attempts, 0)))


__all__ = [
    "INSTITUTION_LOOKBACK_DAYS",
    "enqueue_daily_refresh",
    "enqueue_market_snapshots",
    "ensure_market_snapshots",
    "process_next_job",
    "retry_delay_seconds",
    "run_sync_job",
]
