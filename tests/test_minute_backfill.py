import json
import logging
import re
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeRedis
from sqlalchemy.dialects import postgresql

from quant_signal.application.quote_cache import FUTURES_SYMBOLS, parse_symbols, volume_unit
from quant_signal.infrastructure.providers import shioaji_feed
from quant_signal.infrastructure.providers.shioaji_budget import BudgetExhausted, ShioajiBudget
from quant_signal.infrastructure.providers.shioaji_feed import ShioajiFeed, parse_kbars
from quant_signal.settings import Settings, get_settings
from quant_signal.workers import minute_backfill as mb
from quant_signal.workers.quotes import MinuteBuffer, last_session_refresh, live_minutes_statement

TAIPEI = ZoneInfo("Asia/Taipei")
THURSDAY = date(2026, 11, 26)


def taipei(*args) -> datetime:
    return datetime(*args, tzinfo=TAIPEI)


def wall_ns(moment: datetime) -> int:
    """Shioaji's kbars encoding: Taipei wall time written as if it were UTC."""
    return int(moment.replace(tzinfo=ZoneInfo("UTC")).timestamp() * 1e9)


def kbars(*bars):
    columns = list(zip(*bars, strict=True)) if bars else [[]] * 6
    return SimpleNamespace(
        ts=list(columns[0]),
        Open=list(columns[1]),
        High=list(columns[2]),
        Low=list(columns[3]),
        Close=list(columns[4]),
        Volume=list(columns[5]),
    )


def row(symbol, minute, close=100.0, partial=False):
    return dict(
        symbol=symbol,
        minute=minute,
        open=close,
        high=close,
        low=close,
        close=close,
        first_time=minute,
        last_time=minute + timedelta(seconds=59),
        first_total_volume=0,
        last_total_volume=1,
        is_partial=partial,
    )


def sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


# --- kbars parsing -----------------------------------------------------------------


def test_documented_kbars_example_maps_to_minute_start_in_taipei():
    # From the Shioaji docs: 2330 on 2026-05-18, first bar ts=1779094860000000000 (09:01).
    day = date(2026, 5, 18)
    start, end = mb.official_window("2330.TW", day)
    rows = parse_kbars(
        "2330.TW",
        kbars((1779094860000000000, 2225, 2235, 2225, 2230, 2565)),
        start,
        end,
    )
    assert len(rows) == 1
    assert rows[0]["minute"] == taipei(2026, 5, 18, 9, 0)
    assert rows[0]["minute"].utcoffset() == timedelta(hours=8)
    assert rows[0]["last_time"] < taipei(2026, 5, 18, 9, 1)
    assert (rows[0]["first_total_volume"], rows[0]["last_total_volume"]) == (0, 2565)
    assert rows[0]["is_partial"] is False
    # TXFR1's first documented bar is 08:46 -> the 08:45 minute of the day session.
    futures = parse_kbars(
        "TXF.TAIFEX",
        kbars((1779093960000000000, 40200, 40369, 40169, 40310, 2051)),
        *mb.official_window("TXF.TAIFEX", day),
    )
    assert futures[0]["minute"] == taipei(2026, 5, 18, 8, 45)


def test_kbars_parsing_drops_bad_rows_and_rows_outside_the_window():
    start, end = taipei(2026, 11, 26, 9, 0), taipei(2026, 11, 26, 9, 5)
    rows = parse_kbars(
        "2330.TW",
        kbars(
            (wall_ns(taipei(2026, 11, 26, 9, 1)), 10, 11, 9, 10.5, 3),  # kept: 09:00
            (wall_ns(taipei(2026, 11, 26, 9, 2)), float("nan"), 11, 9, 10, 3),
            (wall_ns(taipei(2026, 11, 26, 9, 3)), 10, 11, 9, 0, 3),  # zero close
            (wall_ns(taipei(2026, 11, 26, 9, 4)), 10, 11, 9, 10, -1),  # negative volume
            (wall_ns(taipei(2026, 11, 26, 9, 5)), 10, 9, 11, 10, 3),  # high below low
            (None, 10, 11, 9, 10, 3),
            ("oops", 10, 11, 9, 10, 3),
            (wall_ns(taipei(2026, 11, 26, 9, 6)), 10, 11, 9, 10, 3),  # 09:05 = end, excluded
            (wall_ns(taipei(2026, 11, 26, 9, 0)), 10, 11, 9, 10, 3),  # 08:59, before start
            (wall_ns(taipei(2026, 11, 26, 9, 5)), Decimal("10"), 11, 9, 10, 4),  # kept: 09:04
        ),
        start,
        end,
    )
    assert [r["minute"].strftime("%H:%M") for r in rows] == ["09:00", "09:04"]
    assert parse_kbars("2330.TW", SimpleNamespace(ts=[1]), start, end) == []


# --- symbols and futures -------------------------------------------------------------


def test_futures_symbol_admission_and_units():
    assert parse_symbols("txf.taifex,2330.TW") == ("2330.TW", "TXF.TAIFEX")
    for invalid in ("TXF", "TXF.TW", "TXFR1.TAIFEX", "MXF.TAIFEX"):
        with pytest.raises(ValueError):
            parse_symbols(invalid)
    assert set(shioaji_feed._FUTURES) == FUTURES_SYMBOLS
    assert volume_unit("TXF.TAIFEX") == "contract"
    assert volume_unit("2330.TW") == "lot"


def fake_sdk(monkeypatch, *, kbars_result=None):
    callbacks = {}
    calls = []
    stock = SimpleNamespace(exchange="TSE", code="2330")
    future = SimpleNamespace(exchange="TAIFEX", code="TXFR1", target_code="TXFL6")
    quote = SimpleNamespace(
        set_on_tick_stk_v1_callback=lambda callback: callbacks.update(stk=callback),
        set_on_tick_fop_v1_callback=lambda callback: callbacks.update(fop=callback),
        set_event_callback=lambda callback: None,
        subscribe=lambda contract, **kwargs: calls.append(("subscribe", contract.code)),
        unsubscribe=lambda contract, **kwargs: calls.append(("unsubscribe", contract.code)),
    )

    def query_kbars(contract, start, end):
        calls.append(("kbars", contract.code, start, end))
        return kbars_result if kbars_result is not None else kbars()

    api = SimpleNamespace(
        quote=quote,
        Contracts=SimpleNamespace(
            Stocks={"2330": stock}, Futures=SimpleNamespace(TXF=SimpleNamespace(TXFR1=future))
        ),
        login=lambda **kwargs: None,
        logout=lambda: None,
        kbars=query_kbars,
    )
    monkeypatch.setitem(
        sys.modules,
        "shioaji",
        SimpleNamespace(
            Shioaji=lambda **kwargs: api,
            constant=SimpleNamespace(
                QuoteType=SimpleNamespace(Tick="tick"), QuoteVersion=SimpleNamespace(v1="v1")
            ),
        ),
    )
    return callbacks, calls


def fop_tick(**overrides):
    values = dict(
        code="TXFL6",
        close=Decimal("23123"),
        pct_chg=Decimal("0.5"),
        total_volume=14160,
        volume=3,
        datetime=datetime(2026, 11, 26, 20, 15, 1),
        open=Decimal("23000"),
        high=Decimal("23200"),
        low=Decimal("22950"),
        simtrade=0,
    )
    return SimpleNamespace(**{**values, **overrides})


def test_futures_ticks_map_the_near_month_contract_to_a_stable_symbol(monkeypatch):
    callbacks, calls = fake_sdk(monkeypatch)
    events = []
    feed = ShioajiFeed(get_settings(), events.append, lambda: None)
    feed.start()
    feed.subscribe("TXF.TAIFEX")
    feed.subscribe("2330.TW")
    assert ("subscribe", "TXFR1") in calls
    callbacks["fop"]("TAIFEX", fop_tick())  # The real contract code (target_code).
    callbacks["fop"]("TAIFEX", fop_tick(code="TXFR1"))
    callbacks["fop"]("TAIFEX", fop_tick(simtrade=1))
    callbacks["fop"]("TSE", fop_tick())
    callbacks["fop"]("TAIFEX", fop_tick(code="MXFL6"))
    callbacks["stk"]("TAIFEX", fop_tick(intraday_odd=0))  # Stock callback never takes it.
    assert len(events) == 2
    event = events[0]
    assert event["symbol"] == "TXF.TAIFEX"
    assert event["unit"] == "contract"
    assert event["price"] == 23123 and event["change_pct"] == 0.005
    assert event["total_volume"] == 14160 and event["volume"] == 3
    assert event["trade_time"] == "2026-11-26T20:15:01+08:00"  # Night session keeps its date.
    assert event["day_open"] == 23000
    json.dumps(event)
    feed.unsubscribe("TXF.TAIFEX")
    assert ("unsubscribe", "TXFR1") in calls
    callbacks["fop"]("TAIFEX", fop_tick())
    assert len(events) == 2
    feed.close()


def test_futures_minutes_count_contracts_and_cross_midnight():
    buffer = MinuteBuffer()
    for second, total in ((1, 100), (30, 104)):
        buffer.add(
            dict(
                symbol="TXF.TAIFEX",
                price=23000 + second,
                total_volume=total,
                volume=2,
                trade_time=taipei(2026, 11, 27, 0, 0, second).isoformat(),
            )
        )
    candle = buffer.candle("TXF.TAIFEX")
    assert candle["volume"] == 6 and candle["volume_unit"] == "contract"
    assert next(iter(buffer.rows))[1] == taipei(2026, 11, 27, 0, 0)
    buffer.add(
        dict(
            symbol="2330.TW",
            price=1,
            total_volume=5,
            volume=1,
            trade_time=taipei(2026, 11, 26, 9, 0, 1).isoformat(),
        )
    )
    assert buffer.candle("2330.TW")["volume"] == 1000  # Stocks: lots -> shares.


def test_session_refresh_runs_before_each_futures_session():
    assert last_session_refresh(taipei(2026, 11, 26, 9, 0)) == taipei(2026, 11, 26, 8, 30)
    assert last_session_refresh(taipei(2026, 11, 26, 15, 0)) == taipei(2026, 11, 26, 14, 55)
    assert last_session_refresh(taipei(2026, 11, 27, 2, 0)) == taipei(2026, 11, 26, 14, 55)


# --- every history query is budgeted -----------------------------------------------


def test_no_shioaji_history_query_bypasses_the_budget():
    source = Path(shioaji_feed.__file__).parents[2]
    pattern = re.compile(r"\.(kbars|ticks|snapshots|credit_enquires|short_stock_sources)\b")
    offenders = [
        f"{path.name}:{number}"
        for path in source.rglob("*.py")
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if pattern.search(line) and not line.lstrip().startswith("#")
    ]
    assert offenders == []  # The feed reaches them only via the budgeted _query().


async def test_minute_bars_go_through_the_budget(monkeypatch):
    bars = kbars((wall_ns(taipei(2026, 11, 27, 15, 1)), 1, 1, 1, 1, 1))
    _, calls = fake_sdk(monkeypatch, kbars_result=bars)
    redis = FakeRedis(taipei(2026, 11, 30, 14, 50).timestamp())
    budget = ShioajiBudget(redis, kbars_session_cap=2, max_wait=0)
    unbudgeted = ShioajiFeed(get_settings(), lambda tick: None, lambda: None)
    unbudgeted.start()
    with pytest.raises(BudgetExhausted):
        await unbudgeted.minute_bars("2330.TW", *mb.official_window("2330.TW", THURSDAY))
    assert not [c for c in calls if c[0] == "kbars"]

    feed = ShioajiFeed(get_settings(), lambda tick: None, lambda: None, budget=budget)
    feed.start()
    monday = date(2026, 11, 30)
    rows = await feed.minute_bars("TXF.TAIFEX", *mb.official_window("TXF.TAIFEX", monday))
    # Monday's trading day starts with Friday's night session.
    assert calls[-1] == ("kbars", "TXFR1", "2026-11-27", "2026-11-30")
    assert [r["minute"] for r in rows] == [taipei(2026, 11, 27, 15, 0)]
    redis.fail = True
    with pytest.raises(BudgetExhausted):
        await feed.minute_bars("2330.TW", *mb.official_window("2330.TW", monday))
    assert len([c for c in calls if c[0] == "kbars"]) == 1


# --- windows -------------------------------------------------------------------------


def test_intraday_windows_follow_each_session():
    assert mb.intraday_window("2330.TW", taipei(2026, 11, 26, 10, 5, 30)) == (
        taipei(2026, 11, 26, 9, 0),
        taipei(2026, 11, 26, 10, 5),
    )
    assert mb.intraday_window("2330.TW", taipei(2026, 11, 26, 8, 50)) is None
    assert mb.intraday_window("2330.TW", taipei(2026, 11, 26, 9, 0, 40)) is None
    assert mb.intraday_window("2330.TW", taipei(2026, 11, 28, 10, 0)) is None  # Saturday
    assert mb.intraday_window("TXF.TAIFEX", taipei(2026, 11, 26, 10, 0)) == (
        taipei(2026, 11, 26, 8, 45),
        taipei(2026, 11, 26, 10, 0),
    )
    assert mb.intraday_window("TXF.TAIFEX", taipei(2026, 11, 26, 20, 10, 5)) == (
        taipei(2026, 11, 26, 15, 0),
        taipei(2026, 11, 26, 20, 10),
    )
    # Friday's night session runs into Saturday morning.
    assert mb.intraday_window("TXF.TAIFEX", taipei(2026, 11, 28, 2, 0)) == (
        taipei(2026, 11, 27, 15, 0),
        taipei(2026, 11, 28, 2, 0),
    )
    assert mb.intraday_window("TXF.TAIFEX", taipei(2026, 11, 28, 9, 0)) is None
    assert mb.intraday_window("TXF.TAIFEX", taipei(2026, 11, 30, 3, 0)) is None  # Monday 03:00


def test_official_windows():
    assert mb.official_window("2330.TW", THURSDAY) == (
        taipei(2026, 11, 26, 0, 0),
        taipei(2026, 11, 27, 0, 0),
    )
    assert mb.official_window("TXF.TAIFEX", THURSDAY) == (
        taipei(2026, 11, 25, 15, 0),
        taipei(2026, 11, 26, 14, 0),
    )


# --- storage ---------------------------------------------------------------------------


def test_storage_statements_protect_each_kind_of_row():
    rows = [row("2330.TW", taipei(2026, 11, 26, 9, 0))]
    live = sql(live_minutes_statement([{**rows[0], "is_partial": True}]))
    assert "ON CONFLICT (symbol, minute) DO UPDATE" in live
    assert live.rstrip().endswith("WHERE quote_minutes.is_partial")
    keep = sql(mb.official_minutes_statement(rows, replace=False))
    assert keep.rstrip().endswith("ON CONFLICT (symbol, minute) DO NOTHING")
    replace = sql(mb.official_minutes_statement(rows, replace=True))
    assert "ON CONFLICT (symbol, minute) DO UPDATE SET" in replace
    for column in mb.UPDATED_COLUMNS:
        assert f"{column} = excluded.{column}" in replace
    assert "WHERE" not in replace.split("DO UPDATE")[1]  # Overwrites partial rows too.


async def test_intraday_backfill_never_writes_the_subscription_minute(monkeypatch):
    stored = []

    async def store(rows, *, replace):
        stored.append((rows, replace))
        return len(rows)

    monkeypatch.setattr(mb, "store_official_minutes", store)
    start, cutoff = mb.intraday_window("2330.TW", taipei(2026, 11, 26, 10, 3, 20))

    async def fetch(symbol, window_start, window_end):
        # A sloppy source returning minutes up to and past the subscription.
        return [row(symbol, taipei(2026, 11, 26, 10, m)) for m in range(0, 6)]

    written = await mb.backfill(fetch, [("2330.TW", start, cutoff)], replace=False, pause=0)
    rows, replace = stored[0]
    assert replace is False
    assert [r["minute"].minute for r in rows] == [0, 1, 2]
    assert all(r["minute"] < cutoff for r in rows)
    assert written == {"2330.TW": 3}


async def test_after_close_backfill_logs_misaligned_official_minutes(monkeypatch, caplog):
    start, end = mb.official_window("2330.TW", THURSDAY)
    minutes = [taipei(2026, 11, 26, 9, m) for m in range(12)]
    official = [row("2330.TW", m, close=100) for m in minutes]
    live = {"rows": [{"minute": m, "close": 100} for m in minutes]}

    async def live_minutes(symbol, window_start, window_end):
        return live["rows"]

    async def store(rows, *, replace):
        return len(rows)

    async def fetch(symbol, window_start, window_end):
        return official

    monkeypatch.setattr(mb, "live_minutes", live_minutes)
    monkeypatch.setattr(mb, "store_official_minutes", store)
    with caplog.at_level(logging.WARNING):
        await mb.backfill(fetch, [("2330.TW", start, end)], replace=True, pause=0)
        assert "disagree" not in caplog.text
        live["rows"] = [{"minute": m + timedelta(minutes=1), "close": 101} for m in minutes]
        await mb.backfill(fetch, [("2330.TW", start, end)], replace=True, pause=0)
    assert "Official minutes disagree with live minutes for 2330.TW: 0/11" in caplog.text
    assert mb.alignment([{"minute": minutes[0], "close": 100}], official) == (1, 1)


# --- scheduling inside the collector ------------------------------------------------------


def backfiller(redis, fetch, now):
    settings = Settings(quotes_backfill_after_close="14:45", quotes_backfill_max_symbols=2)
    return mb.MinuteBackfiller(redis, settings, fetch, now=lambda: now["at"], pause=0)


async def test_intraday_backfill_is_scheduled_once_per_symbol_and_session(monkeypatch):
    redis = FakeRedis()
    now = {"at": taipei(2026, 11, 26, 10, 3, 20)}
    worker = backfiller(redis, None, now)
    assert await worker.on_subscribed("2330.TW") is True
    assert await worker.on_subscribed("2330.TW") is False  # e.g. after a reconnect
    assert await worker.on_subscribed("2317.TW", taipei(2026, 11, 26, 8, 59)) is False
    job = worker.queue.get_nowait()
    assert (job.symbol, job.start, job.end) == (
        "2330.TW",
        taipei(2026, 11, 26, 9, 0),
        taipei(2026, 11, 26, 10, 3),
    )
    assert worker.queue.empty()
    key = next(k for k in redis.values if k.startswith(mb.KEY + "intraday:"))
    assert redis.ttl[key] == mb.DEDUPE_TTL_SECONDS
    redis.fail = True
    assert await worker.on_subscribed("2454.TW") is False  # Fails closed, never raises.


async def test_intraday_backfill_skips_quietly_when_the_budget_is_used_up(monkeypatch, caplog):
    async def fetch(symbol, start, end):
        raise BudgetExhausted("used up")

    worker = backfiller(FakeRedis(), fetch, {"at": taipei(2026, 11, 26, 10, 0)})
    job = mb._Job(
        "intraday",
        symbol="2330.TW",
        start=taipei(2026, 11, 26, 9, 0),
        end=taipei(2026, 11, 26, 10, 0),
    )
    with caplog.at_level(logging.WARNING):
        await worker._intraday(job)
    assert "failed" not in caplog.text


async def test_after_close_runs_once_and_retries_only_what_is_left(monkeypatch):
    redis = FakeRedis()
    now = {"at": taipei(2026, 11, 26, 14, 40)}
    fetched, stored = [], []
    budget = {"left": 1}

    async def fetch(symbol, start, end):
        if budget["left"] <= 0:
            raise BudgetExhausted("used up")
        budget["left"] -= 1
        fetched.append(symbol)
        return [row(symbol, start + timedelta(hours=9))]

    async def store(rows, *, replace):
        stored.append(replace)
        return len(rows)

    async def symbols(day, extra=()):
        assert day == THURSDAY
        return sorted({"2330.TW", "TXF.TAIFEX", "9999.TW", *extra})

    async def live(symbol, start, end):
        return []

    monkeypatch.setattr(mb, "store_official_minutes", store)
    monkeypatch.setattr(mb, "symbols_to_complete", symbols)
    monkeypatch.setattr(mb, "live_minutes", live)
    worker = backfiller(redis, fetch, now)
    assert await worker.check_after_close() is False  # Not yet 14:45.
    now["at"] = taipei(2026, 11, 26, 14, 46)
    assert await worker.check_after_close({"2330.TW"}) is True
    assert await worker.check_after_close() is False  # Claimed for today.
    key = worker.close_key(THURSDAY)
    assert redis.ttl[key] == mb.CLAIM_TTL_SECONDS
    await worker._close(worker.queue.get_nowait())
    # Budget ran out after one symbol: back off, keep progress.
    assert fetched == ["2330.TW"] and stored == [True]
    assert redis.values[key] == "retry" and redis.ttl[key] == mb.RETRY_AFTER_BUDGET_SECONDS
    del redis.values[key]  # The back-off expires.
    budget["left"] = 10
    assert await worker.check_after_close() is True
    await worker._close(worker.queue.get_nowait())
    # Capped at QUOTES_BACKFILL_MAX_SYMBOLS=2 and the finished symbol is not refetched.
    assert fetched == ["2330.TW", "9999.TW"]
    assert redis.values[key] == "done"
    # Once the day is official, a new subscription needs no intraday catch-up.
    assert await worker.on_subscribed("2330.TW", taipei(2026, 11, 26, 15, 0)) is False


async def test_after_close_releases_the_claim_on_failure(monkeypatch):
    redis = FakeRedis()
    now = {"at": taipei(2026, 11, 26, 15, 0)}

    async def broken(day, extra=()):
        raise ConnectionError("database down")

    monkeypatch.setattr(mb, "symbols_to_complete", broken)
    worker = backfiller(redis, None, now)
    assert await worker.check_after_close() is True
    with pytest.raises(ConnectionError):
        await worker._close(worker.queue.get_nowait())
    assert worker.close_key(THURSDAY) not in redis.values
    assert await worker.check_after_close() is True  # Retried on the next check.
    now["at"] = taipei(2026, 11, 28, 15, 0)
    assert (
        await mb.MinuteBackfiller(
            redis, Settings(), None, now=lambda: now["at"]
        ).check_after_close()
        is False
    )  # Weekend.


async def test_worker_runs_jobs_one_at_a_time_and_survives_failures(monkeypatch):
    import asyncio

    active, peak, seen = [0], [0], []

    async def fetch(symbol, start, end):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await asyncio.sleep(0.01)
        active[0] -= 1
        seen.append(symbol)
        if symbol == "2317.TW":
            raise RuntimeError("Shioaji kbars query failed")
        return []

    async def store(rows, *, replace):
        return 0

    monkeypatch.setattr(mb, "store_official_minutes", store)
    worker = backfiller(FakeRedis(), fetch, {"at": taipei(2026, 11, 26, 10, 0)})
    for symbol in ("2330.TW", "2317.TW", "2454.TW"):
        assert await worker.on_subscribed(symbol)
    task = asyncio.create_task(worker.run())
    for _ in range(100):
        if len(seen) == 3:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert seen == ["2330.TW", "2317.TW", "2454.TW"] and peak[0] == 1


def test_backfill_settings_are_validated():
    settings = Settings()
    assert settings.quotes_backfill_enabled is True
    assert settings.quotes_backfill_after_close == "14:45"
    assert settings.quotes_backfill_intraday_max == 150
    assert settings.quotes_backfill_max_symbols == 300
    for bad in (dict(quotes_backfill_after_close="25:00"), dict(quotes_backfill_intraday_max=251)):
        with pytest.raises(ValueError):
            Settings(**bad)


async def test_collector_schedules_backfill_after_subscribing_and_cancels_it(monkeypatch):
    import asyncio

    from quant_signal.application.quote_cache import LOCK
    from quant_signal.workers import quotes

    events = {"subscribed": [], "scheduled": [], "cancelled": False, "budget": None}
    stop = asyncio.Event()

    class Feed:
        def __init__(self, settings, on_tick, on_disconnect, budget=None):
            events["budget"] = budget

        def start(self):
            pass

        def subscribe(self, symbol):
            if symbol == "BAD":
                raise ValueError("invalid")
            events["subscribed"].append(symbol)

        def unsubscribe(self, symbol):
            pass

        def close(self):
            pass

        async def minute_bars(self, symbol, start, end):
            return []

    class Backfiller:
        def __init__(self, redis, settings, fetch):
            pass

        async def on_subscribed(self, symbol, at):
            # Scheduled only after the subscription succeeded.
            assert symbol in events["subscribed"]
            events["scheduled"].append(symbol)
            stop.set()

        async def check_after_close(self, extra=()):
            return False

        async def run(self):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events["cancelled"] = True
                raise

    class Redis:
        async def eval(self, *args):
            return 1

        async def set(self, *args, **kwargs):
            return True

        async def get(self, key):
            return "owner" if key == LOCK else None

        async def zrangebyscore(self, *args):
            return ["BAD", "TXF.TAIFEX"]

    monkeypatch.setattr(shioaji_feed, "ShioajiFeed", Feed)
    monkeypatch.setattr(mb, "MinuteBackfiller", Backfiller)
    async with asyncio.timeout(5):
        await quotes.collect(Redis(), Settings(), "owner", stop)
    assert events["scheduled"] == ["TXF.TAIFEX"]
    assert events["cancelled"] is True
    assert isinstance(events["budget"], ShioajiBudget)
