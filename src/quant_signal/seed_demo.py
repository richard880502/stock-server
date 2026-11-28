from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from quant_signal.domain.models import Bar, MarketObservation
from quant_signal.infrastructure.db import PostgresQuantRepository, session_factory

TAIPEI = ZoneInfo("Asia/Taipei")


def business_days(end: date, count: int) -> list[date]:
    days: list[date] = []
    current = end
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return list(reversed(days))


def make_demo_bars(
    symbol: str,
    days: list[date],
    market_shocks: np.ndarray,
    *,
    start_price: float,
    beta: float,
    drift: float,
    seed: int,
) -> list[Bar]:
    rng = np.random.default_rng(seed)
    idiosyncratic = rng.normal(0, 0.009, len(days))
    returns = drift + market_shocks * beta + idiosyncratic
    closes = start_price * np.exp(np.cumsum(returns))
    bars: list[Bar] = []
    previous_close = start_price
    for index, trading_date in enumerate(days):
        overnight = rng.normal(0, 0.003)
        opening = previous_close * (1 + overnight)
        close = float(closes[index])
        intraday_range = abs(rng.normal(0.012, 0.004))
        high = max(opening, close) * (1 + intraday_range / 2)
        low = min(opening, close) * (1 - intraday_range / 2)
        base_volume = 25_000_000 if symbol == "^TWII" else 8_000_000
        volume = int(base_volume * max(0.2, rng.lognormal(0, 0.35)))
        bars.append(
            Bar(
                symbol=symbol,
                trading_date=trading_date,
                open=round(opening, 4),
                high=round(high, 4),
                low=round(low, 4),
                close=round(close, 4),
                adjusted_close=round(close, 4),
                volume=volume,
                available_at=datetime.combine(
                    trading_date,
                    time(hour=14),
                    tzinfo=TAIPEI,
                ),
                source="synthetic_demo",
            )
        )
        previous_close = close
    return bars


def make_demo_market_observations(
    days: list[date],
    index_bars: list[Bar],
    market_shocks: np.ndarray,
) -> list[MarketObservation]:
    rng = np.random.default_rng(20260732)
    total_issues = 1_000
    margin_balance = 3_000_000_000_000.0
    short_balance = 320_000_000_000.0
    futures_position = 0.0
    observations: list[MarketObservation] = []
    closes = [bar.close for bar in index_bars]
    for index, trading_date in enumerate(days):
        recent20 = closes[max(0, index - 19) : index + 1]
        recent60 = closes[max(0, index - 59) : index + 1]
        trend20 = closes[index] / np.mean(recent20) - 1
        trend60 = closes[index] / np.mean(recent60) - 1
        advance_ratio = float(
            np.clip(
                0.5 + market_shocks[index] * 18 + rng.normal(0, 0.055),
                0.12,
                0.88,
            )
        )
        unchanged = int(np.clip(rng.normal(22, 6), 5, 50))
        directional = total_issues - unchanged
        advancing = int(directional * advance_ratio)
        declining = directional - advancing
        above20_ratio = float(
            np.clip(0.5 + trend20 * 5 + rng.normal(0, 0.05), 0.12, 0.9)
        )
        above60_ratio = float(
            np.clip(0.5 + trend60 * 4 + rng.normal(0, 0.045), 0.1, 0.9)
        )
        high_pressure = max(trend60, 0) * 2 + max(market_shocks[index], 0) * 4
        low_pressure = max(-trend60, 0) * 2 + max(-market_shocks[index], 0) * 4
        new_highs = int(total_issues * np.clip(0.015 + high_pressure, 0.005, 0.14))
        new_lows = int(total_issues * np.clip(0.012 + low_pressure, 0.004, 0.14))
        total_volume = float(max(1, rng.lognormal(np.log(8_000_000_000), 0.18)))
        volume_ratio = float(
            np.clip(0.5 + market_shocks[index] * 15 + rng.normal(0, 0.04), 0.1, 0.9)
        )
        turnover = float(max(100_000_000_000, rng.normal(360_000_000_000, 55_000_000_000)))
        foreign_flow = float(
            market_shocks[index] * 900_000_000_000 + rng.normal(0, 4_500_000_000)
        )
        trust_flow = float(
            market_shocks[index] * 120_000_000_000 + rng.normal(0, 900_000_000)
        )
        dealer_flow = float(
            market_shocks[index] * 150_000_000_000 + rng.normal(0, 1_300_000_000)
        )
        futures_position = float(
            futures_position * 0.92
            + market_shocks[index] * 85_000
            + rng.normal(0, 900)
        )
        margin_balance *= 1 + float(
            np.clip(market_shocks[index] * 0.08 + rng.normal(0, 0.001), -0.006, 0.006)
        )
        short_balance *= 1 + float(
            np.clip(-market_shocks[index] * 0.05 + rng.normal(0, 0.0012), -0.008, 0.008)
        )
        observations.append(
            MarketObservation(
                market="TW",
                trading_date=trading_date,
                index_close=index_bars[index].close,
                total_issues=total_issues,
                advancing_issues=advancing,
                declining_issues=declining,
                unchanged_issues=unchanged,
                above_ma20_issues=int(total_issues * above20_ratio),
                above_ma60_issues=int(total_issues * above60_ratio),
                new_high_52w_issues=new_highs,
                new_low_52w_issues=new_lows,
                up_volume=total_volume * volume_ratio,
                down_volume=total_volume * (1 - volume_ratio),
                turnover_value=turnover,
                foreign_net_flow=foreign_flow,
                investment_trust_net_flow=trust_flow,
                dealer_net_flow=dealer_flow,
                futures_net_open_interest=futures_position,
                margin_balance=margin_balance,
                short_balance=short_balance,
                available_at=datetime.combine(
                    trading_date,
                    time(hour=16),
                    tzinfo=TAIPEI,
                ),
                source="synthetic_demo",
            )
        )
    return observations


async def seed() -> None:
    days = business_days(date.today(), 520)
    rng = np.random.default_rng(20260731)
    market_shocks = rng.normal(0, 0.008, len(days))
    series = [
        ("^TWII", 20_000, 1.0, 0.00025, 1, "Taiwan Weighted Index", "TW"),
        ("0050.TW", 150, 0.95, 0.00022, 2, "Yuanta Taiwan 50 ETF", "TW"),
        ("2330.TW", 900, 1.15, 0.00030, 3, "TSMC Demo Series", "TW"),
    ]
    async with session_factory() as session:
        repository = PostgresQuantRepository(session)
        generated: dict[str, list[Bar]] = {}
        for symbol, price, beta, drift, seed_value, name, market in series:
            bars = make_demo_bars(
                symbol,
                days,
                market_shocks,
                start_price=price,
                beta=beta,
                drift=drift,
                seed=seed_value,
            )
            generated[symbol] = bars
            inserted = await repository.upsert_bars(
                bars,
                instrument_name=name,
                market=market,
            )
            print(f"{symbol}: inserted {inserted} demo bars")
        market_observations = make_demo_market_observations(
            days,
            generated["^TWII"],
            market_shocks,
        )
        inserted = await repository.upsert_market_observations(market_observations)
        print(f"TW: inserted {inserted} demo market observations")


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
