from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from quant_signal.application.market_observations import (
    MarketObservationSyncService,
    compute_breadth,
)
from quant_signal.domain.models import MarketObservation, MarketStockDay
from quant_signal.infrastructure.memory import MemoryQuantRepository

SOURCE = "twse_official"


def _sessions(count: int, start: date = date(2026, 1, 5)) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _stock(symbol: str, day: date, close: float, change: float) -> MarketStockDay:
    return MarketStockDay(
        symbol=symbol,
        name=symbol,
        market="TW",
        trading_date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000,
        turnover_value=close * 1_000,
        change=change,
        available_at=datetime.combine(day, time(10), tzinfo=UTC),
        source=SOURCE,
    )


@dataclass
class _CapitalDay:
    trading_date: date
    index_close: float = 20_000.0
    turnover_value: float = 5e11
    foreign_net_flow: float = -1e9
    investment_trust_net_flow: float = 2e9
    dealer_net_flow: float = 3e8
    margin_balance: float = 3e11
    short_balance: float = 4e5
    capital_data_complete: bool = True


class _Provider:
    def __init__(self, incomplete: set[date] | None = None) -> None:
        self.incomplete = incomplete or set()
        self.calls: list[date] = []

    async def fetch_market_day(self, trading_date: date) -> _CapitalDay | None:
        self.calls.append(trading_date)
        return _CapitalDay(trading_date, capital_data_complete=trading_date not in self.incomplete)


async def _seeded(sessions: int = 65) -> tuple[MemoryQuantRepository, list[date]]:
    repository = MemoryQuantRepository()
    days = _sessions(sessions)
    rows: list[MarketStockDay] = []
    for index, day in enumerate(days):
        # "UP" climbs every day; "DOWN" falls every day; "FLAT" never moves.
        rows += [
            _stock("1101.TW", day, 100 + index, 1.0),
            _stock("1102.TW", day, 200 - index, -1.0),
            _stock("1103.TW", day, 50.0, 0.0),
        ]
    await repository.upsert_market_stock_days(rows)
    return repository, days


def _service(repository, provider, now: datetime, **kwargs) -> MarketObservationSyncService:
    return MarketObservationSyncService(
        repository,
        provider,
        market="TW",
        source=SOURCE,
        history_sessions=60,
        minimum_history=60,
        clock=lambda: now,
        **kwargs,
    )


def test_breadth_counts_ma_participation_and_extremes() -> None:
    rising = deque([float(price) for price in range(1, 61)], maxlen=60)
    falling = deque([float(price) for price in range(60, 0, -1)], maxlen=60)
    short = deque([10.0] * 10, maxlen=60)
    histories = {"UP": rising, "DOWN": falling, "NEW": short}

    result = compute_breadth(
        [("UP", 60.0, 1.0, 100), ("DOWN", 1.0, -1.0, 50), ("NEW", 10.0, 0.0, 5)],
        histories,
        minimum_history=60,
    )

    assert result is not None
    assert result["total"] == 2  # NEW has too little history
    assert result["advancing"] == 1 and result["declining"] == 1
    assert result["above20"] == 1 and result["above60"] == 1
    assert result["new_high"] == 1 and result["new_low"] == 1
    assert result["up_volume"] == 100 and result["down_volume"] == 50


async def test_builds_one_observation_per_session_with_enough_history() -> None:
    repository, days = await _seeded(65)
    provider = _Provider()
    now = datetime.combine(days[-1], time(8), tzinfo=UTC)  # 16:00 Taipei

    report = await _service(repository, provider, now).sync(end=days[-1])

    built = {o.trading_date: o for o in await repository.list_market_observations("TW")}
    # Sessions 60..65 (1-indexed) have 60 closes behind them.
    assert sorted(built) == days[59:]
    assert report.market_observations_inserted == 6
    last = built[days[-1]]
    assert last.total_issues == 3
    assert (last.advancing_issues, last.declining_issues, last.unchanged_issues) == (1, 1, 1)
    assert last.above_ma20_issues == 1 and last.new_high_52w_issues >= 1
    assert last.foreign_net_flow == -1e9 and last.margin_balance == 3e11
    assert last.index_close == 20_000 and last.source == SOURCE


async def test_a_second_run_asks_the_exchange_for_nothing_new() -> None:
    repository, days = await _seeded(65)
    provider = _Provider()
    first_now = datetime.combine(days[-1], time(8), tzinfo=UTC)
    await _service(repository, provider, first_now).sync(end=days[-1])
    provider.calls.clear()

    later = datetime.combine(days[-1] + timedelta(days=3), time(8), tzinfo=UTC)
    report = await _service(repository, provider, later).sync(end=days[-1])

    assert provider.calls == [] and report.market_observations_inserted == 0


async def test_only_newly_stored_sessions_are_observed() -> None:
    repository, days = await _seeded(65)
    provider = _Provider()
    now = datetime.combine(days[-1], time(8), tzinfo=UTC)
    await _service(repository, provider, now).sync(end=days[-1])
    provider.calls.clear()

    new_day = days[-1] + timedelta(days=3)  # next Monday
    await repository.upsert_market_stock_days(
        [
            _stock("1101.TW", new_day, 170, 1.0),
            _stock("1102.TW", new_day, 130, -1.0),
            _stock("1103.TW", new_day, 50.0, 0.0),
        ]
    )
    later = datetime.combine(new_day, time(8), tzinfo=UTC)
    await _service(repository, provider, later).sync(end=new_day)

    assert provider.calls == [new_day]


async def test_incomplete_capital_reports_skip_the_day_without_guessing() -> None:
    repository, days = await _seeded(65)
    provider = _Provider(incomplete={days[-1]})
    now = datetime.combine(days[-1], time(8), tzinfo=UTC)

    report = await _service(repository, provider, now).sync(end=days[-1])

    dates = {o.trading_date for o in await repository.list_market_observations("TW")}
    assert days[-1] not in dates and days[-2] in dates
    assert report.incomplete_capital_dates == [days[-1]]

    # Published later: the same-day miss is retried on the next day and succeeds.
    provider.incomplete.clear()
    provider.calls.clear()
    tomorrow = datetime.combine(days[-1] + timedelta(days=3), time(8), tzinfo=UTC)
    await _service(repository, provider, tomorrow).sync(end=days[-1])
    assert provider.calls == [days[-1]]
    assert days[-1] in {o.trading_date for o in await repository.list_market_observations("TW")}


async def test_waits_until_enough_history_is_stored() -> None:
    repository, days = await _seeded(40)
    provider = _Provider()
    now = datetime.combine(days[-1], time(8), tzinfo=UTC)

    report = await _service(repository, provider, now).sync(end=days[-1])

    assert provider.calls == []
    assert report.market_observations_inserted == 0
    assert any("Waiting for 60 stored sessions" in note for note in report.notes)


async def test_existing_official_observations_are_never_rebuilt() -> None:
    repository, days = await _seeded(65)
    existing = MarketObservation(
        market="TW",
        trading_date=days[59],
        index_close=1.0,
        total_issues=3,
        advancing_issues=1,
        declining_issues=1,
        unchanged_issues=1,
        above_ma20_issues=1,
        above_ma60_issues=1,
        new_high_52w_issues=0,
        new_low_52w_issues=0,
        up_volume=1,
        down_volume=1,
        turnover_value=1,
        foreign_net_flow=0,
        investment_trust_net_flow=0,
        dealer_net_flow=0,
        futures_net_open_interest=0,
        margin_balance=0,
        short_balance=0,
        available_at=datetime.combine(days[59], time(10), tzinfo=UTC),
        source=SOURCE,
    )
    await repository.upsert_market_observations([existing])
    provider = _Provider()
    now = datetime.combine(days[-1], time(8), tzinfo=UTC)

    await _service(repository, provider, now).sync(end=days[-1])

    assert days[59] not in provider.calls
    assert len(provider.calls) == 5


async def test_new_listings_join_the_universe_after_sixty_closes() -> None:
    repository = MemoryQuantRepository()
    days = _sessions(70)
    rows: list[MarketStockDay] = []
    for index, day in enumerate(days):
        rows.append(_stock("1101.TW", day, 100 + index, 1.0))
        rows.append(_stock("1102.TW", day, 200 - index, -1.0))
        if index >= 8:  # lists on the 9th session
            rows.append(_stock("1109.TW", day, 30.0, 0.0))
    await repository.upsert_market_stock_days(rows)
    service = MarketObservationSyncService(
        repository,
        _Provider(),
        market="TW",
        source=SOURCE,
        history_sessions=70,
        minimum_history=65,  # sessions of stored history a date needs
        minimum_stock_history=60,  # closes a stock needs to be counted
        clock=lambda: datetime.combine(days[-1], time(8), tzinfo=UTC),
    )

    await service.sync(end=days[-1])

    totals = {
        o.trading_date: o.total_issues for o in await repository.list_market_observations("TW")
    }
    # The newcomer has 57, 58, 59 closes on sessions 65-67 and 60+ from session 68.
    assert [totals[d] for d in days[64:67]] == [2, 2, 2]
    assert [totals[d] for d in days[67:]] == [3, 3, 3]
