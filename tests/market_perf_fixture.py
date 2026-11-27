"""Seeded whole-market fixtures shared by the market performance tests.

``build_repository`` is deterministic; ``tests/fixtures/sector_golden.json`` was
captured from it with the pre-optimisation implementation (see
``test_market_perf.py::test_sector_output_matches_the_golden_capture``).
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, time, timedelta

from quant_signal.domain.models import IntradaySnapshot, MarketIndustry, MarketStockDay
from quant_signal.infrastructure.memory import MemoryQuantRepository

LAST = date(2026, 10, 30)  # a Friday
MONDAY_IN_SESSION = datetime(2026, 11, 2, 2, 35, tzinfo=UTC)  # 10:35 Taipei
MONDAY_AFTER_CLOSE = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)  # 17:00 Taipei
INDUSTRIES = [("24", "半導體業"), ("14", "建材營造"), ("26", "光電業")]


def sessions(count: int = 32, last: date = LAST) -> list[date]:
    days: list[date] = []
    cursor = last
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


def symbols(stocks: int) -> list[str]:
    return [f"{1000 + index}.{'TWO' if index % 4 == 0 else 'TW'}" for index in range(stocks)]


def build_rows(
    stocks: int = 120, count: int = 32, seed: int = 7, *, last: date = LAST
) -> list[MarketStockDay]:
    rng = random.Random(seed)
    days = sessions(count, last)
    rows: list[MarketStockDay] = []
    for index, symbol in enumerate(symbols(stocks)):
        market = "TWO" if symbol.endswith(".TWO") else "TW"
        # Some names list late or stop trading early; some never publish institutional flow.
        first = rng.choice([0, 0, 0, 0, 5]) if index % 11 == 0 else 0
        stop = len(days) - rng.choice([1, 2]) if index % 17 == 0 else len(days)
        flows = index % 13 != 0
        price = rng.uniform(10, 900)
        for position, day in enumerate(days):
            if position < first or position >= stop:
                continue
            move = rng.gauss(0.0005, 0.02)
            close = round(max(price * (1 + move), 1.0), 2)
            change = round(close - price, 2)
            net = rng.randint(-3_000_000, 3_000_000) if flows and rng.random() > 0.05 else None
            late = day == days[-1]
            rows.append(
                MarketStockDay(
                    symbol=symbol,
                    name=f"N{symbol[:4]}",
                    market=market,  # type: ignore[arg-type]
                    trading_date=day,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=rng.randint(100_000, 50_000_000),
                    turnover_value=rng.uniform(1e6, 5e9),
                    change=change,
                    foreign_net=net,
                    trust_net=None if net is None else rng.randint(-(10**6), 10**6),
                    dealer_net=None if net is None else rng.randint(-(10**6), 10**6),
                    total_net=net,
                    margin_balance=rng.randint(0, 10**5),
                    margin_change=rng.randint(-5000, 5000) if flows else None,
                    short_balance=rng.randint(0, 10**4),
                    short_change=rng.randint(-500, 500) if flows else None,
                    available_at=datetime.combine(day, time(14, 30 if late else 10), tzinfo=UTC),
                    source="tpex_official" if market == "TWO" else "twse_official",
                )
            )
            price = close
    return rows


def build_industries(stocks: int = 120) -> list[MarketIndustry]:
    out: list[MarketIndustry] = []
    for index, symbol in enumerate(symbols(stocks)):
        if index % 9 == 0:
            continue  # unclassified
        code, name = INDUSTRIES[index % len(INDUSTRIES)]
        out.append(
            MarketIndustry(
                symbol=symbol,
                market="TWO" if symbol.endswith(".TWO") else "TW",
                industry_code=code,
                industry_name=name,
                source="twse_openapi",
                available_at=datetime(2026, 10, 1, tzinfo=UTC),
            )
        )
    return out


def build_snapshots(stocks: int = 120, share: float = 0.9, seed: int = 3) -> list[IntradaySnapshot]:
    """Today's (Monday) latest quote for ``share`` of the names, taken 10:30 Taipei."""
    rng = random.Random(seed)
    taken = datetime(2026, 11, 2, 2, 30, tzinfo=UTC)
    return [
        IntradaySnapshot(
            symbol=symbol,
            taken_at=taken,
            price=round(rng.uniform(10, 900), 2),
            change_pct=rng.gauss(0, 0.02),
            volume_lots=rng.randint(10, 50_000),
            amount_twd=rng.uniform(1e6, 2e9),
            available_at=taken + timedelta(seconds=30),
        )
        for index, symbol in enumerate(symbols(stocks))
        if index < int(stocks * share)
    ]


async def build_repository(
    stocks: int = 120, count: int = 32, *, snapshots: float | None = None
) -> MemoryQuantRepository:
    repo = MemoryQuantRepository()
    await repo.upsert_market_stock_days(build_rows(stocks, count))
    await repo.upsert_market_industries(build_industries(stocks))
    if snapshots is not None:
        await repo.upsert_intraday_snapshots(build_snapshots(stocks, snapshots))
    return repo
