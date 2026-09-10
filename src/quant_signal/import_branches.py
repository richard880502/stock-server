from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import UTC, date, datetime, time
from pathlib import Path

from quant_signal.domain.models import BrokerBranchFlow
from quant_signal.infrastructure.db import PostgresQuantRepository, session_factory


def parse_normalized_csv(path: Path, *, source: str) -> list[BrokerBranchFlow]:
    required = {
        "trading_date",
        "symbol",
        "branch_code",
        "branch_name",
        "buy_shares",
        "sell_shares",
        "buy_amount",
        "sell_amount",
    }
    result: list[BrokerBranchFlow] = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"missing CSV columns: {', '.join(sorted(missing))}")
        for row in reader:
            trading_date = date.fromisoformat(row["trading_date"])
            result.append(
                BrokerBranchFlow(
                    symbol=row["symbol"].strip().upper(),
                    trading_date=trading_date,
                    branch_code=row["branch_code"].strip(),
                    branch_name=row["branch_name"].strip(),
                    buy_shares=int(row["buy_shares"]),
                    sell_shares=int(row["sell_shares"]),
                    buy_amount=float(row["buy_amount"]),
                    sell_amount=float(row["sell_amount"]),
                    day_trade_buy_shares=(
                        int(row["day_trade_buy_shares"])
                        if row.get("day_trade_buy_shares")
                        else None
                    ),
                    day_trade_sell_shares=(
                        int(row["day_trade_sell_shares"])
                        if row.get("day_trade_sell_shares")
                        else None
                    ),
                    available_at=(
                        datetime.fromisoformat(row["available_at"])
                        if row.get("available_at")
                        else datetime.combine(
                            trading_date,
                            time(hour=10),
                            tzinfo=UTC,
                        )
                    ),
                    source=source,
                    revision=int(row.get("revision") or 1),
                )
            )
    return result


async def _run(path: Path, source: str) -> None:
    flows = parse_normalized_csv(path, source=source)
    by_symbol: dict[str, list[BrokerBranchFlow]] = {}
    for item in flows:
        by_symbol.setdefault(item.symbol, []).append(item)
    inserted = 0
    async with session_factory() as session:
        repository = PostgresQuantRepository(session)
        for symbol_flows in by_symbol.values():
            inserted += await repository.upsert_broker_branch_flows(symbol_flows)
    print(
        json.dumps(
            {
                "source": source,
                "rows_received": len(flows),
                "rows_upserted": inserted,
                "symbols": sorted(by_symbol),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import a normalized, licensed broker-branch CSV export."
    )
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--source", default="licensed_broker_branch_csv")
    args = parser.parse_args()
    asyncio.run(_run(args.file, args.source))


if __name__ == "__main__":
    main()
