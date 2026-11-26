"""The whole-market after-close job only spends kbars queries off-session."""

import asyncio
import logging
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeRedis

from quant_signal.infrastructure.db.prices import MinuteBar
from quant_signal.infrastructure.providers import shioaji_feed
from quant_signal.infrastructure.providers.shioaji_budget import ShioajiBudget, session_day
from quant_signal.settings import Settings
from quant_signal.workers import minute_backfill as mb

TAIPEI = ZoneInfo("Asia/Taipei")
WEDNESDAY, THURSDAY, FRIDAY = date(2026, 11, 25), date(2026, 11, 26), date(2026, 11, 27)
PROBES = ["TXF.TAIFEX", "2330.TW", "0050.TW"]


def taipei(*args) -> datetime:
    return datetime(*args, tzinfo=TAIPEI)


def close_key(day):
    return mb.MinuteBackfiller.close_key(day)


def row(symbol, minute):
    return MinuteBar(symbol, minute, 1.0, 1.0, 1.0, 1.0, 1, official=True)


def make(redis, fetch, now, max_symbols=2500, **kwargs):
    settings = Settings(
        quotes_backfill_after_close="14:35", quotes_backfill_max_symbols=max_symbols
    )
    return mb.MinuteBackfiller(redis, settings, fetch, now=lambda: now["at"], pause=0, **kwargs)


@pytest.fixture(autouse=True)
def live_day(monkeypatch):
    """Default: the live feed saw the day (no probe). Tests flip ``seen['value']``."""
    seen = {"value": True}

    async def has_live(day):
        return seen["value"]

    monkeypatch.setattr(mb, "day_has_live_activity", has_live)
    return seen


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


def with_rows(symbol, start):
    return [row(symbol, start + timedelta(hours=9))]


def test_off_session_boundaries():
    assert session_day(taipei(2026, 11, 26, 13, 59)) == THURSDAY
    assert session_day(taipei(2026, 11, 26, 14, 29)) == THURSDAY
    assert session_day(taipei(2026, 11, 26, 14, 30)) is None
    assert session_day(taipei(2026, 11, 26, 14, 59)) is None
    assert session_day(taipei(2026, 11, 26, 15, 0)) == THURSDAY
    assert session_day(taipei(2026, 11, 28, 4, 59)) == FRIDAY
    assert session_day(taipei(2026, 11, 28, 5, 0)) is None
    assert session_day(taipei(2026, 11, 28, 15, 0)) is None
    assert mb.off_session(taipei(2026, 11, 26, 14, 40)) is True
    assert mb.off_session(taipei(2026, 11, 26, 10, 0)) is False


def test_the_safety_margin_keeps_two_minutes_clear_of_the_session():
    assert mb.clear_of_session(taipei(2026, 11, 26, 14, 57, 30)) is True
    assert mb.clear_of_session(taipei(2026, 11, 26, 14, 58, 30)) is False
    assert mb.clear_of_session(taipei(2026, 11, 27, 7, 57, 30)) is True
    assert mb.clear_of_session(taipei(2026, 11, 27, 7, 58, 30)) is False
    assert mb.clear_of_session(taipei(2026, 11, 28, 7, 58, 30)) is True  # Saturday.


async def test_close_started_in_session_sends_nothing_and_leaves_a_retry_claim(monkeypatch):
    redis = FakeRedis()
    sent = []

    async def fetch(symbol, start, end):
        sent.append(symbol)
        return with_rows(symbol, start)

    stub_market(monkeypatch, ["2330.TW", "2317.TW"])
    for moment in (taipei(2026, 11, 26, 15, 30), taipei(2026, 11, 26, 14, 58, 30)):
        worker = make(redis, fetch, {"at": moment})
        assert await worker.check_after_close() is False  # Never even claims in-session.
        await worker._close(mb._Job("close", day=THURSDAY))
        assert sent == []
        key = close_key(THURSDAY)
        assert redis.values[key] == "retry" and redis.ttl[key] == mb.RETRY_AFTER_BUDGET_SECONDS
        del redis.values[key]
    worker = make(redis, fetch, {"at": taipei(2026, 11, 26, 14, 57, 30)})
    await worker._close(mb._Job("close", day=THURSDAY))
    assert sent == ["2330.TW", "2317.TW"]  # 14:57:30 is still clear of the session.


async def test_close_stops_when_the_session_gets_close_and_resumes_without_refetching(monkeypatch):
    redis = FakeRedis()
    now = {"at": taipei(2026, 11, 26, 14, 55)}
    sent = []

    async def fetch(symbol, start, end):
        sent.append(symbol)
        now["at"] += timedelta(minutes=1)  # Each query takes a minute.
        return with_rows(symbol, start)

    stub_market(monkeypatch, ["2330.TW", "2317.TW", "2454.TW", "2603.TW"])
    redis.values[close_key(WEDNESDAY)] = "done"
    worker = make(redis, fetch, now)
    assert await worker.check_after_close() is True
    await worker._close(worker.queue.get_nowait())
    assert sent == ["2330.TW", "2317.TW", "2454.TW"]  # 14:55-14:57 run; 14:58 is too close.
    key = close_key(THURSDAY)
    assert redis.values[key] == "retry"
    assert redis.sets[key + ":done"] == set(sent)
    now["at"] = taipei(2026, 11, 27, 5, 10)  # The next morning picks up the rest.
    del redis.values[key]  # The retry back-off has expired.
    assert await worker.check_after_close() is True
    await worker._close(worker.queue.get_nowait())
    assert sent[3:] == ["2603.TW"]
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
    now["at"] = taipei(2026, 12, 1, 6, 0)  # A day older than the previous weekday: never.
    redis.values[close_key(date(2026, 11, 30))] = "done"
    del redis.values[close_key(FRIDAY)]
    assert await worker.check_after_close() is False
    assert worker.queue.empty()


async def test_today_and_the_previous_weekday_can_both_be_queued():
    worker = make(FakeRedis(), None, {"at": taipei(2026, 11, 26, 14, 36)})
    assert await worker.check_after_close() is True
    assert [worker.queue.get_nowait().day, worker.queue.get_nowait().day] == [
        WEDNESDAY,
        THURSDAY,
    ]


async def test_a_full_queue_releases_the_claim_and_never_raises():
    redis = FakeRedis()
    worker = make(redis, None, {"at": taipei(2026, 11, 26, 14, 36)})
    worker.queue = asyncio.Queue(maxsize=1)
    worker.queue.put_nowait(mb._Job("intraday"))
    assert await worker.check_after_close() is False
    assert close_key(THURSDAY) not in redis.values and close_key(WEDNESDAY) not in redis.values


async def test_cancelling_run_releases_the_claims_of_queued_close_jobs(monkeypatch):
    redis = FakeRedis()
    started = asyncio.Event()

    async def blocked(day, extra=(), whole_market=True):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(mb, "symbols_to_complete", blocked)
    worker = make(redis, None, {"at": taipei(2026, 11, 26, 14, 36)})
    assert await worker.check_after_close() is True  # Wednesday and Thursday queued.
    task = asyncio.create_task(worker.run())
    await started.wait()
    assert worker.queue.qsize() == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert close_key(WEDNESDAY) not in redis.values and close_key(THURSDAY) not in redis.values


# --- holiday detection ---------------------------------------------------------------------


async def test_a_day_is_probed_with_three_fixed_instruments_and_needs_two_empty_answers(
    monkeypatch, live_day
):
    live_day["value"] = False
    redis = FakeRedis()
    now = {"at": taipei(2026, 11, 26, 14, 36)}
    sent = []

    async def fetch(symbol, start, end):
        sent.append(symbol)
        return []

    async def usage():
        return True

    stub_market(monkeypatch, ["1101.TW", "2330.TW", "2317.TW"])
    redis.values[close_key(WEDNESDAY)] = "done"
    worker = make(redis, fetch, now, usage_probe=usage)
    assert await worker.check_after_close() is True
    await worker._close(worker.queue.get_nowait())
    assert sent == PROBES  # Fixed order, exactly three queries, no main loop.
    key = close_key(THURSDAY)
    assert redis.values[key] == "closed" and redis.ttl[key] == mb.CLOSED_TTL_SECONDS == 6 * 3600
    assert await worker.check_after_close() is False  # "closed" blocks a retry for 6 hours.
    # The next off-session window: the second all-empty probe confirms.
    now["at"] = taipei(2026, 11, 27, 5, 10)
    del redis.values[key]  # The 6 hours are over.
    assert await worker.check_after_close() is True
    await worker._close(worker.queue.get_nowait())
    assert sent == PROBES * 2
    assert redis.values[key] == "done" and redis.ttl[key] == mb.DONE_TTL_SECONDS


async def test_one_probe_with_rows_makes_it_a_trading_day(monkeypatch, live_day):
    live_day["value"] = False
    redis = FakeRedis()
    sent = []

    async def fetch(symbol, start, end):
        sent.append(symbol)
        return with_rows(symbol, start) if symbol == "0050.TW" else []

    async def usage():
        return True

    stub_market(monkeypatch, ["1101.TW", "2330.TW"])
    worker = make(redis, fetch, {"at": taipei(2026, 11, 26, 14, 36)}, usage_probe=usage)
    await worker._close(mb._Job("close", day=THURSDAY))
    key = close_key(THURSDAY)
    assert redis.values[key] == "done"
    assert "0050.TW" in redis.sets[key + ":done"]
    assert sent == [*PROBES, "1101.TW", "2330.TW"]  # Whole job ran; 0050 is not refetched.


async def test_a_probe_that_raises_is_unknown_not_empty(monkeypatch, live_day):
    live_day["value"] = False
    redis = FakeRedis()

    async def fetch(symbol, start, end):
        if symbol == "2330.TW":
            raise RuntimeError("suspended")
        return []

    async def usage():
        return True

    stub_market(monkeypatch, ["1101.TW"])
    worker = make(redis, fetch, {"at": taipei(2026, 11, 26, 14, 36)}, usage_probe=usage)
    await worker._close(mb._Job("close", day=THURSDAY))
    key = close_key(THURSDAY)
    assert redis.values[key] == "retry"
    assert key + ":probe" not in redis.values  # Nothing was judged.


@pytest.mark.parametrize("answer", [None, False])
async def test_unknown_or_low_usage_is_never_judged(monkeypatch, live_day, answer):
    live_day["value"] = False
    redis = FakeRedis()
    sent = []

    async def fetch(symbol, start, end):
        sent.append(symbol)
        return []

    async def usage():
        return answer

    stub_market(monkeypatch, ["1101.TW"])
    worker = make(redis, fetch, {"at": taipei(2026, 11, 26, 14, 36)}, usage_probe=usage)
    await worker._close(mb._Job("close", day=THURSDAY))
    assert sent == [] and redis.values[close_key(THURSDAY)] == "retry"


async def test_empty_answers_never_close_a_day_the_live_feed_saw(monkeypatch, live_day):
    live_day["value"] = True
    redis = FakeRedis()
    sent = []

    async def fetch(symbol, start, end):
        sent.append(symbol)
        return []

    stub_market(monkeypatch, ["2330.TW", "1101.TW", "2317.TW"])
    worker = make(redis, fetch, {"at": taipei(2026, 11, 26, 14, 36)})
    await worker._close(mb._Job("close", day=THURSDAY))
    key = close_key(THURSDAY)
    assert sent == ["2330.TW", "1101.TW", "2317.TW"]  # The whole job ran, no probe judging.
    assert redis.values[key] != "closed" and key + ":probe" not in redis.values


# --- failures ------------------------------------------------------------------------------


async def test_ten_failures_in_a_row_release_the_day_without_marking_it_done(monkeypatch):
    redis = FakeRedis()
    sent = []

    async def fetch(symbol, start, end):
        sent.append(symbol)
        raise RuntimeError("boom")

    stub_market(monkeypatch, [f"{1000 + i}.TW" for i in range(50)])
    worker = make(redis, fetch, {"at": taipei(2026, 11, 26, 14, 36)})
    await worker._close(mb._Job("close", day=THURSDAY))
    assert len(sent) == 10
    assert redis.values[close_key(THURSDAY)] == "retry"


async def test_too_many_scattered_failures_do_not_mark_the_day_done(monkeypatch):
    redis = FakeRedis()
    calls = {"n": 0}

    async def fetch(symbol, start, end):
        calls["n"] += 1
        if calls["n"] % 3 == 0:
            raise RuntimeError("boom")
        return with_rows(symbol, start)

    stub_market(monkeypatch, [f"{1000 + i}.TW" for i in range(60)])
    worker = make(redis, fetch, {"at": taipei(2026, 11, 26, 14, 36)})
    await worker._close(mb._Job("close", day=THURSDAY))
    assert redis.values[close_key(THURSDAY)] == "retry"
    assert len(redis.sets[close_key(THURSDAY) + ":done"]) == 40  # Progress is kept.


async def test_a_few_failures_still_finish_the_day(monkeypatch):
    redis = FakeRedis()
    calls = {"n": 0}

    async def fetch(symbol, start, end):
        calls["n"] += 1
        if calls["n"] in (7, 50):
            raise RuntimeError("boom")
        return with_rows(symbol, start)

    stub_market(monkeypatch, [f"{1000 + i}.TW" for i in range(100)])
    worker = make(redis, fetch, {"at": taipei(2026, 11, 26, 14, 36)})
    await worker._close(mb._Job("close", day=THURSDAY))
    assert redis.values[close_key(THURSDAY)] == "done"


async def test_progress_is_logged_every_200_symbols(monkeypatch, caplog):
    async def fetch(symbol, start, end):
        return with_rows(symbol, start)

    stub_market(monkeypatch, [f"{1000 + i}.TW" for i in range(450)])
    worker = make(FakeRedis(), fetch, {"at": taipei(2026, 11, 26, 14, 40)}, max_symbols=450)
    with caplog.at_level(logging.INFO):
        await worker._close(mb._Job("close", day=THURSDAY))
    assert "2026-11-26: 200/450" in caplog.text and "2026-11-26: 400/450" in caplog.text
    assert "After-close minute backfill: 450 symbols" in caplog.text


async def test_whole_market_job_never_touches_the_session_kbars_counter(monkeypatch):
    redis = FakeRedis()
    clock = {"at": taipei(2026, 11, 26, 14, 35)}
    redis.now = clock["at"].timestamp()
    budget = ShioajiBudget(redis, kbars_session_cap=150, max_wait=5)
    sent = []

    async def fetch(symbol, start, end):
        assert session_day(clock["at"]) is None  # Never sent in-session...
        assert session_day(clock["at"] + timedelta(minutes=2)) is None  # ...or near it.
        await budget.acquire("kbars")
        sent.append(symbol)
        clock["at"] += timedelta(seconds=3)
        redis.now = clock["at"].timestamp()
        return with_rows(symbol, start)

    stub_market(monkeypatch, [f"{1000 + i}.TW" for i in range(2000)])
    redis.values[close_key(WEDNESDAY)] = "done"
    worker = make(redis, fetch, clock)
    assert await worker.check_after_close() is True
    await worker._close(worker.queue.get_nowait())
    assert 300 < len(sent) < 2000  # Stopped at 14:58 (3 s per query).
    assert await budget.used("kbars", THURSDAY) == 0
    assert redis.values[close_key(THURSDAY)] == "retry"


# --- manual CLI ----------------------------------------------------------------------------


def run_cli(monkeypatch, start, symbols):
    clock = {"at": start}
    sent = []

    class FakeFeed:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def close(self):
            pass

        def usage(self):
            return None

        async def minute_bars(self, symbol, start, end):
            sent.append(symbol)
            clock["at"] += timedelta(minutes=1)
            return with_rows(symbol, start)

    async def aclose():
        return None

    async def many(day, extra=(), whole_market=True):
        return symbols

    stub_market(monkeypatch, symbols)
    monkeypatch.setattr(mb, "symbols_to_complete", many)
    monkeypatch.setattr(mb, "taipei_now", lambda: clock["at"])
    monkeypatch.setattr(mb, "QUERY_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(shioaji_feed, "ShioajiFeed", FakeFeed)
    monkeypatch.setattr(sys, "argv", ["backfill"])
    monkeypatch.setitem(
        sys.modules,
        "redis.asyncio",
        SimpleNamespace(
            Redis=SimpleNamespace(from_url=lambda *a, **k: SimpleNamespace(aclose=aclose))
        ),
    )
    return sent


def test_manual_cli_refuses_a_whole_market_run_in_session(monkeypatch):
    sent = run_cli(monkeypatch, taipei(2026, 11, 26, 10, 0), ["2330.TW"])
    with pytest.raises(SystemExit) as stopped:
        mb.main()
    assert "off-session" in str(stopped.value) and sent == []


def test_manual_cli_stops_when_the_session_gets_close(monkeypatch):
    sent = run_cli(monkeypatch, taipei(2026, 11, 26, 14, 55), [f"{1000 + i}.TW" for i in range(10)])
    with pytest.raises(SystemExit) as stopped:
        mb.main()
    assert "about to start" in str(stopped.value)
    assert len(sent) == 3  # 14:55, 14:56, 14:57 run; 14:58 is within the margin.
