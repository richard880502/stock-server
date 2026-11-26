from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, time

from quant_signal.backtest import BacktestEngine
from quant_signal.domain.models import Bar
from quant_signal.infrastructure.db import PostgresQuantRepository, session_factory
from quant_signal.live_bar import (
    EX_RIGHTS_NOTE,
    TAIPEI,
    build_provisional_bar,
    ex_rights_suspected,
)
from quant_signal.settings import get_settings
from quant_signal.workers import sync as sync_worker

logger = logging.getLogger(__name__)


async def live_today_bar(repository, spec, bars: list[Bar], as_of: datetime):
    """Provisional bar for today (Taipei) or None. Never touches Shioaji; reads only
    the minute rows the collector stored, up to the job's request time ``as_of``."""
    if not spec.include_live_today or not get_settings().quotes_enabled:
        return None
    today = as_of.astimezone(TAIPEI).date()
    if spec.end < today or any(
        b.trading_date == today and b.source != "synthetic_demo" for b in bars
    ):
        return None
    since = datetime.combine(today, time.min, TAIPEI)
    try:
        minutes = await repository.list_quote_minutes(spec.symbol, since=since, until=as_of)
    except Exception:
        logger.warning("live minutes unavailable for %s", spec.symbol, exc_info=True)
        return None
    official = [b for b in bars if b.source != "synthetic_demo"] or bars
    factor = official[-1].adjustment_factor if official else 1.0
    built = build_provisional_bar(
        spec.symbol, today, minutes, as_of=as_of, adjustment_factor=factor
    )
    if built is None:
        return None
    if not official or ex_rights_suspected(built[0], official[-1].close):
        # Fail safe: unknown reference price or possible ex-date -> no provisional bar.
        return None, EX_RIGHTS_NOTE
    return built


async def process_once() -> bool:
    async with session_factory() as session:
        repository = PostgresQuantRepository(session)
        job = await repository.claim_next_backtest_job()
        if job is None:
            return False
        try:
            bars = await repository.list_bars(job.spec.symbol, end=job.spec.end)
            provisional_at = None
            live = await live_today_bar(repository, job.spec, bars, job.created_at)
            provisional_note = None
            if live is not None and live[0] is None:
                provisional_note = live[1]
            elif live is not None:
                bars = [*bars, live[0]]
                provisional_at = live[1]
            benchmark_bars = (
                await repository.list_bars(job.spec.benchmark, end=job.spec.end)
                if job.spec.benchmark
                else None
            )
            market_observations = (
                await repository.list_market_observations(
                    job.spec.market,
                    end=job.spec.end,
                )
                if job.spec.market
                else None
            )
            institutional_flows = (
                await repository.list_institutional_flows(
                    job.spec.symbol,
                    end=job.spec.end,
                )
                if job.spec.use_chip_filter
                else None
            )
            branch_flows = (
                await repository.list_broker_branch_flows(
                    job.spec.symbol,
                    end=job.spec.end,
                )
                if job.spec.use_chip_filter
                else None
            )
            report = BacktestEngine().run(
                bars,
                job.spec,
                benchmark_bars=benchmark_bars,
                provisional_as_of=provisional_at,
                provisional_note=provisional_note,
                market_observations=market_observations,
                institutional_flows=institutional_flows,
                branch_flows=branch_flows,
            )
            await repository.complete_backtest_job(job.id, report)
            logger.info("completed backtest %s", job.id)
        except Exception as exc:
            logger.exception("backtest %s failed", job.id)
            await repository.fail_backtest_job(job.id, str(exc))
        return True


async def run_backtests_forever() -> None:
    settings = get_settings()
    while True:
        processed = await process_once()
        if not processed:
            await asyncio.sleep(settings.backtest_poll_seconds)


async def run_forever() -> None:
    """Run backtests and, unless disabled, the upstream sync queue side by side."""
    if get_settings().sync_enabled:
        await asyncio.gather(run_backtests_forever(), sync_worker.run_forever())
    else:
        await run_backtests_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(process_once() if args.once else run_forever())


if __name__ == "__main__":
    main()
