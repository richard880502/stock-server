"""The whole-market after-close job only spends kbars queries off-session."""

import logging
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeRedis

from quant_signal.infrastructure.db.prices import MinuteBar
from quant_signal.infrastructure.providers.shioaji_budget import ShioajiBudget, session_day
from quant_signal.settings import Settings
from quant_signal.workers import minute_backfill as mb

TAIPEI = ZoneInfo("Asia/Taipei")
WEDNESDAY, THURSDAY, FRIDAY = date(2026, 11, 25), date(2026, 11, 26), date(2026, 11, 27)


def taipei(*args) -> datetime:
    return datetime(*args, tzinfo=TAIPEI)


def close_key(day):
    return mb.MinuteBackfiller.close_key(day)


def row(symbol, minute):
    return MinuteBar(symbol, minute, 1.0, 1.0, 1.0, 1.0, 1, official=True)


def make(redis, fetch, now, max_symbols=2500):
    settings = Settings(
        quotes_backfill_after_close="13:55", quotes_backfill_max_symbols=max_symbols
    )
    return mb.MinuteBackfiller(redis, settings, fetch, now=lambda: now["at"], pause=0)


def stub_market(monkeypatch, symbols_list):
    async def store(rows, *, replace):
        return len(rows)

    async def symbols(day, extra=(), whole_market=True):
        return list(symbols_list)

    async def live(symbol, start, end):
        return []

    monkeypatch.setattr(mb, "store_official_minutes", store)
    monkeypatch.setattr(mb, "symbols_to_complete", symbols)
    monkeypatch.setattr(mb, "live_minutes", live)


def test_off_session_boundaries():
    assert session_day(taipei(2026, 11, 26, 13, 49)) == THURSDAY
    assert session_day(taipei(2026, 11, 26, 13, 50)) is None
    assert session_day(taipei(2026, 11, 26, 14, 59)) is None
    assert session_day(taipei(2026, 11, 26, 15, 0)) == THURSDAY
    assert session_day(taipei(2026, 11, 28, 4, 59)) == FRIDAY
    assert session_day(taipei(2026, 11, 28, 5, 0)) is None
    assert session_day(taipei(2026, 11, 28, 15, 0)) is None
    assert mb.off_session(taipei(2026, 11, 26, 14, 0)) is True
    assert mb.off_session(taipei(2026, 11, 26, 10, 0)) is False


async def test_close_started_in_session_sends_nothing_and_leaves_a_retry_claim(monkeypatch):
    redis = FakeRedis()
    sent = []

    async def fetch(symbol, start, end):
        sent.append(symbol)
        return [row(symbol, start + timedelta(hours=9))]

    stub_market(monkeypatch, ["2330.TW", "2317.TW"])
    worker = make(redis, fetch, {"at": taipei(2026, 11, 26, 15, 30)})  # Night session.
    assert await worker.check_after_close() is False  # Never even claims in-session.
    await worker._close(mb._Job("close", day=THURSDAY))
    assert sent == []
    key = close_key(THURSDAY)
    assert redis.values[key] == "retry" and redis.ttl[key] == mb.RETRY_AFTER_BUDGET_SECONDS


async def test_close_stops_when_the_session_starts_and_resumes_without_refetching(monkeypatch):
    redis = FakeRedis()
    now = {"at": taipei(2026, 11, 26, 14, 58)}
    sent = []

    async def fetch(symbol, start, end):
        sent.append(symbol)
        now["at"] += timedelta(minutes=1)  # Each query takes a minute: 15:00 arrives.
        return [row(symbol, start + timedelta(hours=9))]

    stub_market(monkeypatch, ["2330.TW", "2317.TW", "2454.TW"])
    redis.values[close_key(WEDNESDAY)] = "done"
    worker = make(redis, fetch, now)
    assert await worker.check_after_close() is True
    await worker._close(worker.queue.get_nowait())
    assert sent == ["2330.TW", "2317.TW"]  # 14:58 and 14:59 run; 15:00 stops.
    key = close_key(THURSDAY)
    assert redis.values[key] == "retry"
    assert redis.sets[key + ":done"] == {"2330.TW", "2317.TW"}
    # Friday 05:10: the previous weekday is picked up and only the rest is fetched.
    now["at"] = taipei(2026, 11, 27, 5, 10)
    del redis.values[key]  # The retry back-off has expired.
    assert await worker.check_after_close() is True
    await worker._close(worker.queue.get_nowait())
    assert sent == ["2330.TW", "2317.TW", "2454.TW"]
    assert redis.values[key] == "done"


async def test_previous_weekday_is_resumed_only_when_unfinished_and_recent():
    redis = FakeRedis()
    now = {"at": taipei(2026, 11, 27, 5, 10)}  # Friday morning.
    worker = make(redis, None, now)
    assert await worker.check_after_close() is True
    assert worker.queue.get_nowait().day == THURSDAY and worker.queue.empty()
    assert await worker.check_after_close() is False  # Claimed: not queued twice.
    redis.values[close_key(THURSDAY)] = "done"
    assert await worker.check_after_close() is False  # Done: not queued.
    now["at"] = taipei(2026, 11, 28, 6, 0)  # Saturday: Friday can still finish.
    assert await worker.check_after_close() is True
    assert worker.queue.get_nowait().day == FRIDAY
    now["at"] = taipei(2026, 11, 30, 6, 0)  # Monday resumes Friday, not the weekend.
    del redis.values[close_key(FRIDAY)]
    assert await worker.check_after_close() is True
    assert worker.queue.get_nowait().day == FRIDAY
    redis.values[close_key(FRIDAY)] = "done"
    assert await worker.check_after_close() is False  # Monday does not redo Friday.
    # A day older than the previous weekday is never retried.
    now["at"] = taipei(2026, 12, 1, 6, 0)
    redis.values[close_key(date(2026, 11, 30))] = "done"
    del redis.values[close_key(FRIDAY)]
    assert await worker.check_after_close() is False
    assert worker.queue.empty()


async def test_today_and_the_previous_weekday_can_both_be_queued():
    worker = make(FakeRedis(), None, {"at": taipei(2026, 11, 26, 13, 56)})
    assert await worker.check_after_close() is True
    assert [worker.queue.get_nowait().day, worker.queue.get_nowait().day] == [
        WEDNESDAY,
        THURSDAY,
    ]


async def test_a_holiday_is_closed_after_25_empty_symbols(monkeypatch, caplog):
    redis = FakeRedis()
    sent = []

    async def fetch(symbol, start, end):
        sent.append(symbol)
        return []

    stub_market(monkeypatch, [f"{1000 + i}.TW" for i in range(100)])
    worker = make(redis, fetch, {"at": taipei(2026, 11, 26, 14, 0)}, max_symbols=100)
    with caplog.at_level(logging.INFO):
        await worker._close(mb._Job("close", day=THURSDAY))
    assert len(sent) == 25
    assert redis.values[close_key(THURSDAY)] == "done"
    assert caplog.text.count("closed day") == 1


async def test_progress_is_logged_every_200_symbols(monkeypatch, caplog):
    async def fetch(symbol, start, end):
        return [row(symbol, start + timedelta(hours=9))]

    stub_market(monkeypatch, [f"{1000 + i}.TW" for i in range(450)])
    worker = make(FakeRedis(), fetch, {"at": taipei(2026, 11, 26, 14, 0)}, max_symbols=450)
    with caplog.at_level(logging.INFO):
        await worker._close(mb._Job("close", day=THURSDAY))
    assert "2026-11-26: 200/450" in caplog.text and "2026-11-26: 400/450" in caplog.text
    assert "After-close minute backfill: 450 symbols" in caplog.text


async def test_whole_market_job_never_touches_the_session_kbars_counter(monkeypatch):
    redis = FakeRedis()
    clock = {"at": taipei(2026, 11, 26, 13, 55)}
    redis.now = clock["at"].timestamp()
    budget = ShioajiBudget(redis, kbars_session_cap=150, max_wait=5)
    sent = []

    async def fetch(symbol, start, end):
        await budget.acquire("kbars")
        sent.append(symbol)
        clock["at"] += timedelta(seconds=3)
        redis.now = clock["at"].timestamp()
        return [row(symbol, start + timedelta(hours=9))]

    stub_market(monkeypatch, [f"{1000 + i}.TW" for i in range(2000)])
    redis.values[close_key(WEDNESDAY)] = "done"
    worker = make(redis, fetch, clock)
    assert await worker.check_after_close() is True
    await worker._close(worker.queue.get_nowait())
    # 13:55 + 3 s per query reaches 15:00 after 1,300 queries and the job stops there.
    assert 1000 < len(sent) < 2000
    assert session_day(clock["at"]) is not None
    assert await budget.used("kbars", THURSDAY) == 0
    assert redis.values[close_key(THURSDAY)] == "retry"


async def _noop():
    return None


def test_manual_cli_refuses_a_whole_market_run_in_session(monkeypatch):
    monkeypatch.setattr(mb, "taipei_now", lambda: taipei(2026, 11, 26, 10, 0))
    monkeypatch.setattr(sys, "argv", ["backfill"])
    monkeypatch.setitem(
        sys.modules,
        "redis.asyncio",
        SimpleNamespace(
            Redis=SimpleNamespace(from_url=lambda *a, **k: SimpleNamespace(aclose=_noop))
        ),
    )
    with pytest.raises(SystemExit) as stopped:
        mb.main()
    assert "off-session" in str(stopped.value)
