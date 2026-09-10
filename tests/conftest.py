from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from quant_signal.domain.models import Bar, MarketObservation


def make_bars(
    *,
    symbol: str = "TEST",
    count: int = 320,
    start: date = date(2025, 1, 1),
    daily_return: float = 0.001,
) -> list[Bar]:
    bars: list[Bar] = []
    price = 100.0
    current = start
    while len(bars) < count:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue
        opening = price * (1 + daily_return / 3)
        close = price * (1 + daily_return)
        bars.append(
            Bar(
                symbol=symbol,
                trading_date=current,
                open=opening,
                high=max(opening, close) * 1.005,
                low=min(opening, close) * 0.995,
                close=close,
                adjusted_close=close,
                volume=1_000_000 + len(bars) * 1_000,
                available_at=datetime.combine(
                    current,
                    time(hour=6),
                    tzinfo=UTC,
                ),
                source="test",
            )
        )
        price = close
        current += timedelta(days=1)
    return bars


def make_market_observations(
    *,
    count: int = 320,
    start: date = date(2025, 1, 1),
    advancing_ratio: float = 0.68,
    participation_ratio: float = 0.72,
) -> list[MarketObservation]:
    observations: list[MarketObservation] = []
    current = start
    index_close = 20_000.0
    margin_balance = 3_000_000_000_000.0
    short_balance = 300_000_000_000.0
    while len(observations) < count:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue
        directional = 980
        advancing = int(directional * advancing_ratio)
        index_close *= 1.001
        margin_balance *= 1.0002
        short_balance *= 0.9999
        observations.append(
            MarketObservation(
                market="TW",
                trading_date=current,
                index_close=index_close,
                total_issues=1_000,
                advancing_issues=advancing,
                declining_issues=directional - advancing,
                unchanged_issues=20,
                above_ma20_issues=int(1_000 * participation_ratio),
                above_ma60_issues=int(1_000 * (participation_ratio - 0.05)),
                new_high_52w_issues=80,
                new_low_52w_issues=10,
                up_volume=7_000_000_000,
                down_volume=3_000_000_000,
                turnover_value=350_000_000_000,
                foreign_net_flow=8_000_000_000 + len(observations) * 10_000_000,
                investment_trust_net_flow=1_000_000_000,
                dealer_net_flow=500_000_000,
                futures_net_open_interest=5_000 + len(observations) * 10,
                margin_balance=margin_balance,
                short_balance=short_balance,
                available_at=datetime.combine(
                    current,
                    time(hour=8),
                    tzinfo=UTC,
                ),
                source="test",
            )
        )
        current += timedelta(days=1)
    return observations


@pytest.fixture
def trending_bars() -> list[Bar]:
    return make_bars()


@pytest.fixture
def healthy_market() -> list[MarketObservation]:
    return make_market_observations()
