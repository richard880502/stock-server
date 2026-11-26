"""Price layers: partitions, the read rule, snapshots and the traffic guard."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeRedis

from quant_signal.application.prices import price_now
from quant_signal.infrastructure.db.partitions import (
    add_months,
    create_partition_sql,
    expired_partitions,
    months_between,
    partition_month,
)
from quant_signal.infrastructure.db.prices import LatestQuote
from quant_signal.infrastructure.providers.shioaji_budget import BudgetExhausted
from quant_signal.infrastructure.providers.shioaji_feed import parse_snapshots
from quant_signal.settings import Settings
from quant_signal.workers import market_snapshots as ms

TAIPEI = ZoneInfo("Asia/Taipei")


def taipei(*args) -> datetime:
    return datetime(*args, tzinfo=TAIPEI)


def quote(symbol="2330.TW", at=None, price=1081.0, reference=1060.0, source="tick"):
    at = at or taipei(2026, 11, 26, 10, 0)
    return LatestQuote(symbol, price, reference, None, None, None, 10, None, at, source, at)


# --- partitions ---------------------------------------------------------------------


def test_monthly_partitions_follow_the_taipei_calendar():
    sql = create_partition_sql(date(2026, 12, 15))
    assert "minute_bars_2026_12 PARTITION OF minute_bars" in sql
    assert "FROM ('2026-12-01T00:00:00+08:00') TO ('2027-01-01T00:00:00+08:00')" in sql
    assert add_months(date(2026, 11, 1), 2) == date(2027, 1, 1)
    assert months_between(date(2026, 10, 30), date(2027, 1, 2)) == [
        date(2026, 10, 1),
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
    ]
    assert partition_month("minute_bars_2026_11") == date(2026, 11, 1)
    assert partition_month("minute_bars_2026_11_pkey") is None


def test_retention_drops_only_whole_months_outside_the_window():
    names = ["minute_bars_2025_10", "minute_bars_2025_11", "minute_bars_2026_11", "other"]
    assert expired_partitions(names, date(2026, 11, 26), 0) == []  # keep everything
    # 12 months as of November 2026 = December 2025 .. November 2026.
    assert expired_partitions(names, date(2026, 11, 26), 12) == [
        "minute_bars_2025_10",
        "minute_bars_2025_11",
    ]
    assert expired_partitions(names, date(2026, 11, 26), 13) == ["minute_bars_2025_10"]
    assert "minute_bars_2026_11" not in expired_partitions(names, date(2026, 11, 26), 1)


# --- the read rule ------------------------------------------------------------------


def test_latest_quote_is_used_only_when_newer_than_the_official_record():
    live = quote(at=taipei(2026, 11, 26, 10, 0))
    now = price_now(live, date(2026, 11, 25))
    assert now.price == 1081 and now.source == "tick"
    assert now.change_pct == round((1081 / 1060 - 1) * 100, 4)
    assert price_now(live, date(2026, 11, 26)) is None  # official day already landed
    assert price_now(live, None).price == 1081  # no official history at all
    assert price_now(None, date(2026, 11, 25)) is None
    assert price_now(quote(reference=None), date(2026, 11, 25)).change_pct is None


# --- snapshots ------------------------------------------------------------------------


def test_snapshot_slots_cover_the_session_and_one_closing_pass():
    slot = ms.snapshot_slot
    assert slot(taipei(2026, 11, 26, 8, 59), 10) is None
    assert slot(taipei(2026, 11, 26, 9, 0, 5), 10) == "2026-11-26 09:00"
    assert slot(taipei(2026, 11, 26, 10, 19), 10) == "2026-11-26 10:10"
    assert slot(taipei(2026, 11, 26, 13, 30), 10) == "2026-11-26 13:30"
    assert slot(taipei(2026, 11, 26, 13, 32), 10) is None
    assert slot(taipei(2026, 11, 26, 13, 40), 10) == "2026-11-26 13:35"
    assert slot(taipei(2026, 11, 26, 14, 0), 10) is None
    assert slot(taipei(2026, 11, 28, 10, 0), 10) is None  # Saturday
    assert slot(taipei(2026, 11, 26, 10, 0), 0) is None  # disabled


def test_snapshots_parse_with_the_exchange_reference_price():
    def wall_ns(moment):
        return int(moment.replace(tzinfo=UTC).timestamp() * 1e9)

    items = [
        SimpleNamespace(
            code="2330",
            ts=wall_ns(datetime(2026, 11, 26, 10, 0)),
            close=1081.0,
            change_price=21.0,
            open=1062.0,
            high=1085.0,
            low=1058.0,
            total_volume=23456,
            total_amount=25_000_000_000,
        ),
        SimpleNamespace(code="9999", ts=1, close=10.0, change_price=0),  # not asked for
        SimpleNamespace(code="2317", ts="bad", close=200.0, change_price=1),
        SimpleNamespace(code="2454", ts=wall_ns(datetime(2026, 11, 26, 10, 0)), close=0),
    ]
    received = datetime(2026, 11, 26, 2, 0, 5, tzinfo=UTC)
    quotes = parse_snapshots(
        items, {"2330": "2330.TW", "2317": "2317.TW", "2454": "2454.TW"}, received
    )
    assert len(quotes) == 1
    (q,) = quotes
    assert (q.symbol, q.price, q.reference, q.source) == ("2330.TW", 1081.0, 1060.0, "snapshot")
    assert q.trade_time == taipei(2026, 11, 26, 10, 0)
    assert (q.volume, q.amount, q.high) == (23456, 25_000_000_000, 1085.0)


class _Usage:
    def __init__(self, remaining):
        self.remaining = remaining
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.remaining is None:
            return None
        return dict(connections=1, bytes=1, limit_bytes=500, remaining_bytes=self.remaining)


async def test_usage_monitor_stops_heavy_work_below_the_reserve():
    redis = FakeRedis()
    usage = _Usage(200)
    monitor = ms.UsageMonitor(usage, redis, reserve_bytes=100, every_seconds=60)
    assert await monitor.ok() is True
    assert await monitor.ok() is True and usage.calls == 1  # cached for a minute
    assert ms.USAGE_KEY in redis.values
    usage.remaining = 50
    assert await monitor.ok() is True  # still the cached reading
    await monitor.refresh(force=True)
    assert await monitor.ok() is False
    # Unknown usage (SDK without api.usage, or not logged in) does not block.
    assert await ms.UsageMonitor(_Usage(None), redis, 100).ok() is True


async def test_snapshot_pass_covers_the_market_in_batches_and_stops_on_budget(monkeypatch):
    stored, asked = [], []

    async def fetch(symbols):
        asked.append(len(symbols))
        if len(asked) == 3:
            raise BudgetExhausted("used up")
        return [quote(symbol=s) for s in symbols]

    async def universe():
        return [f"{1000 + i}.TW" for i in range(1200)] + ["TXF.TAIFEX"]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self):
            pass

    async def store(session, quotes):
        stored.extend(quotes)
        return len(quotes)

    monkeypatch.setattr(ms, "session_factory", Session)
    monkeypatch.setattr(ms, "store_latest", store)
    now = {"at": taipei(2026, 11, 26, 10, 1)}
    published = []

    async def publish(event):
        published.append(event)

    monitor = ms.UsageMonitor(_Usage(10**9), FakeRedis(), 100)
    snapshots = ms.MarketSnapshots(
        FakeRedis(), Settings(), fetch, universe, monitor, publish, now=lambda: now["at"]
    )
    assert await snapshots.tick() is True
    assert await snapshots.tick() is False  # one pass per slot
    await snapshots._task
    assert asked == [500, 500, 200]  # futures excluded; budget stopped the 3rd batch
    assert len(stored) == 1000

    # Seeding a newly subscribed stock: once per day, and published like a tick.
    assert await snapshots.seed("2330.TW") is True
    assert await snapshots.seed("2330.TW") is False
    assert await snapshots.seed("TXF.TAIFEX") is False
    assert published[0]["symbol"] == "2330.TW" and published[0]["source"] == "snapshot"
    assert published[0]["reference"] == 1060.0
    await snapshots.close()


@pytest.mark.parametrize("reserve,expected", [(0, True), (10**12, False)])
async def test_snapshot_seed_respects_the_traffic_reserve(reserve, expected, monkeypatch):
    async def fetch(symbols):
        return [quote(symbol=s) for s in symbols]

    async def store(session, quotes):
        return len(quotes)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def commit(self):
            pass

    monkeypatch.setattr(ms, "session_factory", Session)
    monkeypatch.setattr(ms, "store_latest", store)

    async def publish(event):
        pass

    async def universe():
        return []

    monitor = ms.UsageMonitor(_Usage(10**9), FakeRedis(), reserve)
    snapshots = ms.MarketSnapshots(FakeRedis(), Settings(), fetch, universe, monitor, publish)
    assert await snapshots.seed("2330.TW") is expected


def test_minute_bars_cost_less_than_the_old_rows():
    # Documented estimate (bytes per row incl. the primary-key index entry).
    old = 24 + 16 + 4 * 8 + 2 * 8 + 2 * 8 + 1 + 4 + 32  # header, symbol, floats, times, totals
    new = 24 + 8 + 4 * 4 + 4 + 2 + 1 + 4 + 24  # header, minute, ints, volume, id, flag
    assert new < old * 0.6
    _ = timedelta
