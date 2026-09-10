from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import date, timedelta

from quant_signal.application.data_sync import (
    TpexDataSyncService,
    TwseDataSyncService,
)
from quant_signal.infrastructure.db import PostgresQuantRepository, session_factory
from quant_signal.infrastructure.providers import TpexProvider, TwseProvider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import point-in-time market data from official TWSE and TPEx endpoints."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    symbol = subparsers.add_parser(
        "symbol",
        help="Backfill .TW/.TWO stock, ^TWII, or ^TWOII",
    )
    symbol.add_argument("--symbol", required=True)
    symbol.add_argument("--start", type=date.fromisoformat, required=True)
    symbol.add_argument("--end", type=date.fromisoformat, default=date.today())

    market = subparsers.add_parser(
        "market",
        help="Build real TWSE breadth and capital observations",
    )
    market.add_argument("--start", type=date.fromisoformat, required=True)
    market.add_argument("--end", type=date.fromisoformat, default=date.today())
    market.add_argument("--warmup-calendar-days", type=int, default=400)
    market.add_argument("--market", choices=["TW", "TWO"], default="TW")

    institutions = subparsers.add_parser(
        "institutions",
        help="Backfill official stock-level foreign/trust/dealer flows",
    )
    institutions.add_argument("--symbol", required=True)
    institutions.add_argument("--start", type=date.fromisoformat, required=True)
    institutions.add_argument("--end", type=date.fromisoformat, default=date.today())

    daily = subparsers.add_parser(
        "daily",
        help="Sync ^TWII, selected symbols, and recent market observations",
    )
    daily.add_argument("--symbols", nargs="+", default=["2330.TW", "6488.TWO"])
    daily.add_argument("--markets", nargs="+", choices=["TW", "TWO"], default=["TW"])
    daily.add_argument("--lookback-days", type=int, default=500)
    return parser


async def _run(args: argparse.Namespace) -> None:
    async with (
        session_factory() as session,
        TwseProvider() as twse_provider,
        TpexProvider() as tpex_provider,
    ):
        repository = PostgresQuantRepository(session)
        twse = TwseDataSyncService(repository, twse_provider)
        tpex = TpexDataSyncService(repository, tpex_provider)
        reports = []
        if args.command == "symbol":
            normalized = args.symbol.strip().upper()
            service = (
                tpex
                if normalized.endswith(".TWO") or normalized == "^TWOII"
                else twse
            )
            reports.append(
                await service.sync_symbol(
                    args.symbol,
                    start=args.start,
                    end=args.end,
                )
            )
        elif args.command == "market":
            service = tpex if args.market == "TWO" else twse
            reports.append(
                await service.sync_market_environment(
                    start=args.start,
                    end=args.end,
                    warmup_calendar_days=args.warmup_calendar_days,
                )
            )
        elif args.command == "institutions":
            normalized = args.symbol.strip().upper()
            service = tpex if normalized.endswith(".TWO") else twse
            reports.append(
                await service.sync_institutional_flows(
                    normalized,
                    start=args.start,
                    end=args.end,
                )
            )
        else:
            end = date.today()
            start = end - timedelta(days=args.lookback_days)
            symbols = list(args.symbols)
            if any(symbol.upper().endswith(".TW") for symbol in symbols):
                symbols.append("^TWII")
            if any(symbol.upper().endswith(".TWO") for symbol in symbols):
                symbols.append("^TWOII")
            for symbol in dict.fromkeys(symbols):
                service = (
                    tpex
                    if symbol.upper().endswith(".TWO") or symbol.upper() == "^TWOII"
                    else twse
                )
                reports.append(
                    await service.sync_symbol(symbol, start=start, end=end)
                )
                if not symbol.startswith("^"):
                    reports.append(
                        await service.sync_institutional_flows(
                            symbol,
                            start=end - timedelta(days=100),
                            end=end,
                        )
                    )
            for market_code in dict.fromkeys(args.markets):
                service = tpex if market_code == "TWO" else twse
                reports.append(
                    await service.sync_market_environment(
                        start=end - timedelta(days=100),
                        end=end,
                    )
                )
        print(
            json.dumps(
                [report.model_dump(mode="json") for report in reports],
                ensure_ascii=False,
                indent=2,
            )
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
