from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeRedis

from quant_signal.infrastructure.providers import shioaji_budget
from quant_signal.infrastructure.providers.shioaji_budget import (
    WINDOW_KEY,
    BudgetExhausted,
    ShioajiBudget,
    session_day,
)

TAIPEI = ZoneInfo("Asia/Taipei")


def at(*args) -> float:
    return datetime(*args, tzinfo=TAIPEI).timestamp()


def test_session_day_reads_the_session_broadly():
    thursday = date(2026, 11, 26)
    assert session_day(datetime(2026, 11, 26, 7, 59, tzinfo=TAIPEI)) is None
    assert session_day(datetime(2026, 11, 26, 8, 0, tzinfo=TAIPEI)) == thursday
    assert session_day(datetime(2026, 11, 26, 14, 29, tzinfo=TAIPEI)) == thursday
    assert session_day(datetime(2026, 11, 26, 14, 30, tzinfo=TAIPEI)) is None  # After close.
    assert session_day(datetime(2026, 11, 26, 14, 59, tzinfo=TAIPEI)) is None
    assert session_day(datetime(2026, 11, 26, 15, 0, tzinfo=TAIPEI)) == thursday  # Night opens.
    assert session_day(datetime(2026, 11, 28, 4, 59, tzinfo=TAIPEI)) == date(2026, 11, 27)
    assert session_day(datetime(2026, 11, 28, 5, 0, tzinfo=TAIPEI)) is None
    assert session_day(datetime(2026, 11, 26, 20, 0, tzinfo=TAIPEI)) == thursday  # Night.
    # The night session after midnight counts toward the day it started on.
    assert session_day(datetime(2026, 11, 27, 4, 59, tzinfo=TAIPEI)) == thursday
    assert session_day(datetime(2026, 11, 28, 3, 0, tzinfo=TAIPEI)) == date(2026, 11, 27)
    assert session_day(datetime(2026, 11, 28, 10, 0, tzinfo=TAIPEI)) is None  # Saturday.
    assert session_day(datetime(2026, 11, 30, 3, 0, tzinfo=TAIPEI)) is None  # Sunday night.


def test_budget_refuses_settings_above_shioaji_limits():
    with pytest.raises(ValueError):
        ShioajiBudget(FakeRedis(), kbars_session_cap=270)
    with pytest.raises(ValueError):
        ShioajiBudget(FakeRedis(), kbars_session_cap=10, ticks_session_cap=11)
    with pytest.raises(ValueError):
        ShioajiBudget(FakeRedis(), kbars_session_cap=10, window_limit=50)


async def test_rolling_window_allows_40_per_10_seconds_then_skips():
    redis = FakeRedis(at(2026, 11, 26, 15, 0))  # Outside the day session.
    redis.now = at(2026, 11, 26, 14, 40)
    budget = ShioajiBudget(redis, kbars_session_cap=150, max_wait=0)
    for _ in range(40):
        await budget.acquire("snapshots")
        redis.now += 0.01
    with pytest.raises(BudgetExhausted):
        await budget.acquire("kbars")
    assert len(redis.zsets[WINDOW_KEY]) == 40  # The refused attempt left no trace.
    redis.now += 10
    await budget.acquire("kbars")  # The oldest calls have left the window.
    assert await budget.used("kbars", date(2026, 11, 26)) == 0  # Not a session query.


async def test_rolling_window_waits_for_a_free_slot(monkeypatch):
    redis = FakeRedis(at(2026, 11, 26, 14, 40))
    budget = ShioajiBudget(redis, kbars_session_cap=150, max_wait=15)
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        redis.now += seconds

    monkeypatch.setattr(shioaji_budget.asyncio, "sleep", sleep)
    for _ in range(40):
        await budget.acquire("kbars")
    await budget.acquire("kbars")
    assert slept and sum(slept) >= 9.9
    window = redis.zsets[WINDOW_KEY].values()
    assert len([s for s in window if s > redis.now - 10]) <= 40


async def test_session_caps_for_kbars_and_ticks():
    redis = FakeRedis(at(2026, 11, 26, 10, 0))
    budget = ShioajiBudget(redis, kbars_session_cap=3, max_wait=0)
    for _ in range(3):
        await budget.acquire("kbars")
        redis.now += 1
    with pytest.raises(BudgetExhausted):
        await budget.acquire("kbars")
    assert await budget.used("kbars", date(2026, 11, 26)) == 3
    for _ in range(8):
        await budget.acquire("ticks")
        redis.now += 1
    with pytest.raises(BudgetExhausted):
        await budget.acquire("ticks")
    await budget.acquire("snapshots")  # Snapshots only count toward the window.
    # The night session shares the same day's allowance.
    redis.now = at(2026, 11, 26, 21, 0)
    with pytest.raises(BudgetExhausted):
        await budget.acquire("kbars")
    # After the close the session cap does not apply; the next day starts fresh.
    redis.now = at(2026, 11, 27, 14, 45)
    await budget.acquire("kbars")
    redis.now = at(2026, 11, 27, 9, 30)
    await budget.acquire("kbars")
    assert await budget.used("kbars", date(2026, 11, 27)) == 1


async def test_session_cap_holds_under_racing_callers():
    redis = FakeRedis(at(2026, 11, 26, 10, 0))
    budget = ShioajiBudget(redis, kbars_session_cap=2, max_wait=0)
    redis.values[shioaji_budget.PREFIX + "kbars:2026-11-26"] = "1"
    await budget.acquire("kbars")
    # Another process got in between our check and our increment.
    redis.values[shioaji_budget.PREFIX + "kbars:2026-11-26"] = "1"
    original = redis.get

    async def stale_get(key):
        return "0" if key.endswith("kbars:2026-11-26") else await original(key)

    redis.get = stale_get
    await budget.acquire("kbars")
    with pytest.raises(BudgetExhausted):
        await budget.acquire("kbars")  # incr says 3 > 2: refused even with a stale read.


async def test_budget_fails_closed_without_redis():
    redis = FakeRedis(at(2026, 11, 26, 10, 0))
    redis.fail = True
    with pytest.raises(BudgetExhausted):
        await ShioajiBudget(redis, kbars_session_cap=150).acquire("kbars")
    with pytest.raises(ValueError):
        await ShioajiBudget(FakeRedis(), kbars_session_cap=150).acquire("orders")
