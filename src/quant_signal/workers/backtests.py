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
    provisional_factor,
)
from quant_signal.settings import get_settings

logger = logging.getLogger(__name__)


async def live_today_bar(repository, spec, bars: list[Bar], as_of: datetime):
    """Provisional bar for today (Taipei), ``(None, note)`` when unsafe, or None.

    Never touches Shioaji: reads only the minute bars and latest reference price the
    ingest service stored, up to the job's request time ``as_of``.
    """
    if not spec.include_live_today:
        return None
    today = as_of.astimezone(TAIPEI).date()
    if spec.end < today or any(
        b.trading_date == today and b.source != "synthetic_demo" for b in bars
    ):
        return None
    since = datetime.combine(today, time.min, TAIPEI)
    try:
        minutes = await repository.list_minute_bars(spec.symbol, since=since, until=as_of)
        latest = (await repository.latest_quotes([spec.symbol])).get(spec.symbol.upper())
    except Exception:
        logger.warning("live minutes unavailable for %s", spec.symbol, exc_info=True)
        return None
    official = [b for b in bars if b.source != "synthetic_demo"]
    if not official:
        return None  # No base to continue from.
    previous = official[-1]
    today_reference = (
        latest.reference
        if latest is not None and latest.trade_time.astimezone(TAIPEI).date() == today
        else None
    )
    factor = provisional_factor(previous.adjustment_factor, previous.close, today_reference)
    built = build_provisional_bar(
        spec.symbol, today, minutes, as_of=as_of, adjustment_factor=factor or 1.0
    )
    if built is None or factor is None:
        return None
    if today_reference is None and ex_rights_suspected(built[0], previous.close):
        # No reference price to correct an apparent ex-date: leave today out.
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
    """Run queued backtests. Market data is written only by the ingest service."""
    await run_backtests_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(process_once() if args.once else run_forever())


if __name__ == "__main__":
    main()
