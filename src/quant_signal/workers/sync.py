from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta

from quant_signal.application.derivatives_data import refresh_derivatives_data
from quant_signal.application.fundamentals_data import (
    refresh_fundamentals_data,
    trading_session_open,
)
from quant_signal.application.holders_data import refresh_holders_data
from quant_signal.application.market_industry import refresh_industries
from quant_signal.application.official_events import refresh_official_data
from quant_signal.application.ports import QuantRepository
from quant_signal.application.screener import refresh_screener
from quant_signal.application.short_data import refresh_short_data
from quant_signal.application.sync_jobs import (
    enqueue_daily_refresh,
    enqueue_market_repairs,
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
# Older dates without a snapshot (their market rows were repaired late) built per upkeep,
# outside the trading session only: each is a whole-market compute.
SCREENER_GAPS_PER_UPKEEP = 2
# How often an idle worker looks for market days stored empty while the other exchange
# traded, or recent days still without ETF rows (see ``enqueue_market_repairs``); outside
# the trading session only. A completed market sync makes the next idle tick check at once,
# so a first ETF fill (``ETF_BACKFILL_DAYS`` per run) takes consecutive jobs, not 30 min each.
MARKET_REPAIR_EVERY = timedelta(minutes=30)
_market_repair_attempt: dict[str, datetime] = {"at": datetime.min.replace(tzinfo=UTC)}


async def _refresh_screener(
    repository: QuantRepository, *, recompute_latest: bool, fill_gaps: int = 0
) -> None:
    settings = get_settings()
    done = await refresh_screener(
        repository,
        enabled=settings.screener_enabled,
        retention_sessions=settings.screener_retention_sessions,
        backfill_sessions=settings.screener_backfill_sessions,
        recompute_latest=recompute_latest,
        settled=_screener_settled,
        fill_gaps=fill_gaps,
    )
    if done:
        logger.info("screener snapshot computed for %s", ", ".join(d.isoformat() for d in done))


async def _after_market_sync(repository: QuantRepository) -> None:
    # Flow and credit reports arrive after the quotes, so every market sync rebuilds the
    # newest date.
    await _refresh_screener(repository, recompute_latest=True)
    # Re-check right away for what is still left (an ETF backfill bigger than one run).
    # Terminates: every run records the days it asked for, ``ok`` or ``empty`` (which then
    # waits ``EMPTY_RETRY_AFTER``), and a failed job never reaches this hook.
    _market_repair_attempt["at"] = datetime.min.replace(tzinfo=UTC)


_holders_attempts: dict[str, datetime] = {}
_short_data_attempts: dict[str, datetime] = {}
_fundamentals_attempts: dict[str, datetime] = {}
_derivatives_attempts: dict[str, datetime] = {}
_derivatives_specs: dict[str, object] = {}


async def _rollback_quietly(repository: QuantRepository) -> None:
    """Best effort: a cancelled statement can leave the shared session needing a rollback."""
    rollback = getattr(getattr(repository, "session", None), "rollback", None)
    if rollback is None:
        return
    try:
        await rollback()
    except Exception:
        logger.exception("rollback after an interrupted side feed failed")


async def _run_feed(
    name: str,
    repository: QuantRepository,
    budget: float,
    feed: Callable[[], Awaitable[object]],
) -> None:
    """Run one side feed with a total wall-clock budget; it can never fail or stall the tick.

    A feed commits what it finished (every write is its own transaction) and resumes from
    ``fetch_coverage`` on the next tick, so cancelling it at the budget loses nothing.
    """
    try:
        await asyncio.wait_for(feed(), timeout=budget)
    except TimeoutError:
        logger.warning(
            "%s refresh used its %.0f s budget; moving on, it resumes next tick", name, budget
        )
        await _rollback_quietly(repository)
    except Exception:
        # Side feeds never stall the price sync.
        logger.exception("%s refresh failed", name)


async def run_side_feeds(
    repository: QuantRepository,
    twse: TwseProvider,
    tpex: TpexProvider,
    taifex: TaifexClient | None,
    now: datetime,
) -> None:
    """The official-data side feeds, one after another, each within its own time budget."""
    settings = get_settings()
    budget = settings.side_feed_budget_seconds
    if settings.official_events_enabled:
        await _run_feed(
            "official events",
            repository,
            budget,
            lambda: refresh_official_data(
                repository,
                twse,
                tpex,
                now=now,
                attempts=_official_attempts,
                material_minutes=settings.official_events_material_minutes,
                retry_after=timedelta(minutes=settings.official_events_retry_minutes),
            ),
        )
    if settings.holders_tdcc_enabled or settings.holders_foreign_enabled:
        await _run_feed(
            "holders data",
            repository,
            budget,
            lambda: refresh_holders_data(
                repository,
                twse,
                tpex,
                now=now,
                attempts=_holders_attempts,
                tdcc_enabled=settings.holders_tdcc_enabled,
                foreign_enabled=settings.holders_foreign_enabled,
                foreign_window=settings.holders_foreign_days,
                retry_after=timedelta(minutes=settings.holders_retry_minutes),
            ),
        )
    await _run_feed(
        "short data",
        repository,
        budget,
        lambda: refresh_short_data(
            repository,
            twse,
            tpex,
            now=now,
            attempts=_short_data_attempts,
            sbl_enabled=settings.short_data_sbl_enabled,
            daytrade_enabled=settings.short_data_daytrade_enabled,
            insider_enabled=settings.short_data_insider_enabled,
            dividend_enabled=settings.short_data_dividend_enabled,
            backfill_sessions=settings.short_data_backfill_sessions,
            backfill_per_tick=settings.short_data_backfill_per_tick,
            retry_after=timedelta(minutes=settings.short_data_retry_minutes),
        ),
    )
    if (
        settings.fundamentals_valuation_enabled
        or settings.fundamentals_revenue_history_enabled
        or settings.fundamentals_financials_enabled
    ):
        await _run_feed(
            "fundamentals",
            repository,
            budget,
            lambda: refresh_fundamentals_data(
                repository,
                twse,
                tpex,
                now=now,
                attempts=_fundamentals_attempts,
                valuation_enabled=settings.fundamentals_valuation_enabled,
                valuation_backfill_sessions=settings.valuation_backfill_sessions,
                revenue_history_enabled=settings.fundamentals_revenue_history_enabled,
                revenue_backfill_months=settings.revenue_backfill_months,
                financials_enabled=settings.fundamentals_financials_enabled,
                financials_backfill_quarters=settings.financials_backfill_quarters,
                backfill_per_tick=settings.fundamentals_backfill_per_tick,
                retry_after=timedelta(minutes=settings.fundamentals_retry_minutes),
                mops_interval=settings.fundamentals_mops_interval_seconds,
            ),
        )
    if settings.derivatives_enabled and taifex is not None:
        await _run_feed(
            "derivatives data",
            repository,
            budget,
            lambda: refresh_derivatives_data(
                repository,
                taifex,
                now=now,
                attempts=_derivatives_attempts,
                specs_cache=_derivatives_specs,
                backfill_sessions=settings.derivatives_backfill_sessions,
                after_close=settings.derivatives_after_close,
                request_budget=settings.derivatives_requests_per_tick,
                retry_after=timedelta(minutes=settings.derivatives_retry_minutes),
            ),
        )


async def tick(
    twse: TwseProvider, tpex: TpexProvider, taifex: TaifexClient | None = None
) -> bool | None:
    """One scheduling pass: recover stuck jobs, queue the daily refresh, run one job, then
    the side feeds.

    The price sync comes first: the side feeds are bounded individually, but a slow
    exchange must never delay the quotes (and the realtime collector shares this loop).
    """
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
            # Classification is a weekly nicety; never let it stall the price sync.
            await _run_feed(
                "industry classification",
                repository,
                settings.side_feed_budget_seconds,
                lambda: refresh_industries(repository, twse, tpex, now=now),
            )
        job_error: Exception | None = None
        outcome: bool | None = None
        try:
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
        except Exception as exc:  # the side feeds still run; the failure is re-raised below
            job_error = exc
            await _rollback_quietly(repository)
        await run_side_feeds(repository, twse, tpex, taifex, datetime.now(UTC))
        if job_error is not None:
            raise job_error
        quiet = not trading_session_open(now)
        if outcome is None and quiet and now - _market_repair_attempt["at"] >= MARKET_REPAIR_EVERY:
            # Idle: a day stored empty while the other exchange traded was a failed fetch,
            # and recent days may lack ETF rows; queue the market so the next tick fetches
            # them (no exchange request here).
            _market_repair_attempt["at"] = now
            try:
                await enqueue_market_repairs(
                    repository,
                    now=now,
                    lookback_days=settings.sync_market_lookback_days,
                    providers={"TW": twse, "TWO": tpex},
                )
            except Exception:
                logger.exception("market repair check failed")
                await _rollback_quietly(repository)
        if outcome is None and now - _screener_attempt["at"] >= SCREENER_UPKEEP_EVERY:
            # Idle: catch up on any date the snapshot is missing (first run, downtime,
            # market days repaired after newer snapshots were built).
            _screener_attempt["at"] = now
            await _refresh_screener(
                repository,
                recompute_latest=False,
                fill_gaps=SCREENER_GAPS_PER_UPKEEP if quiet else 0,
            )
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
