from __future__ import annotations

import argparse
import asyncio
import logging

from quant_signal.backtest import BacktestEngine
from quant_signal.infrastructure.db import PostgresQuantRepository, session_factory
from quant_signal.settings import get_settings

logger = logging.getLogger(__name__)


async def process_once() -> bool:
    async with session_factory() as session:
        repository = PostgresQuantRepository(session)
        job = await repository.claim_next_backtest_job()
        if job is None:
            return False
        try:
            bars = await repository.list_bars(job.spec.symbol, end=job.spec.end)
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


async def run_forever() -> None:
    settings = get_settings()
    while True:
        processed = await process_once()
        if not processed:
            await asyncio.sleep(settings.backtest_poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(process_once() if args.once else run_forever())


if __name__ == "__main__":
    main()
