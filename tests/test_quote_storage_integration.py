"""Run against isolated Redis/Postgres through QUOTES_TEST_* environment variables."""

import asyncio
import json
import os
import time
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from quant_signal.application.quote_cache import (
    ADMIT,
    DEMAND,
    HEALTH,
    LOCK,
    PREFIX,
    VIEWER_ADMIT,
    QuoteHub,
)
from quant_signal.infrastructure.db.models import QuoteMinuteRow
from quant_signal.settings import Settings
from quant_signal.workers import quotes


@pytest.mark.skipif(not os.getenv("QUOTES_TEST_REDIS_URL"), reason="isolated Redis required")
async def test_shared_demand_capacity_fencing_and_hub_cleanup():
    redis = Redis.from_url(os.environ["QUOTES_TEST_REDIS_URL"], decode_responses=True)
    settings = Settings(
        quotes_redis_url=SecretStr(os.environ["QUOTES_TEST_REDIS_URL"]), quotes_max_symbols=2
    )
    first, second = QuoteHub(settings), QuoteHub(settings)
    try:
        await redis.delete(DEMAND, HEALTH, LOCK, PREFIX + "2330.TW")
        await first.join(("2330.TW",))
        await second.join(("2330.TW", "6488.TWO"))
        assert await redis.zcard(DEMAND) == 2
        viewer = PREFIX + "viewers:test"
        await redis.delete(viewer)
        assert await redis.eval(VIEWER_ADMIT, 1, viewer, time.time(), 1, "tab-1") == 1
        assert await redis.eval(VIEWER_ADMIT, 1, viewer, time.time(), 1, "tab-2") == 0
        await redis.delete(viewer)
        with pytest.raises(ValueError):
            await first.join(("2317.TW",))
        assert await redis.zcard(DEMAND) == 2
        await redis.set(LOCK, "new-owner")
        event = json.dumps(dict(symbol="2330.TW", price=100, trade_epoch=123))
        args = (quotes.PUBLISH, 3, LOCK, PREFIX + "2330.TW", PREFIX + "updates")
        assert await redis.eval(*args, "old-owner", event) == 0
        assert await redis.eval(*args, "new-owner", event) == 1
        older = json.dumps(dict(symbol="2330.TW", price=90, trade_epoch=122))
        assert await redis.eval(*args, "new-owner", older) == 2
        await redis.set(HEALTH, "live")
        await asyncio.sleep(1.1)
        assert first.quotes["2330.TW"]["price"] == 100
        assert second.quotes["2330.TW"]["price"] == 100
        first.leave(("2330.TW",))
        assert not first.viewers
        assert second.viewers["2330.TW"] == 1
        await redis.zadd(DEMAND, {"2330.TW": time.time() - 1})
        assert await redis.eval(ADMIT, 1, DEMAND, time.time(), 2, "2317.TW") == 1
    finally:
        await first.close()
        await second.close()
        await redis.delete(DEMAND, HEALTH, LOCK, PREFIX + "2330.TW")
        await redis.aclose()


@pytest.mark.skipif(not os.getenv("QUOTES_TEST_DATABASE_URL"), reason="isolated Postgres required")
async def test_minute_upsert_restart_and_concurrent_callback(monkeypatch):
    engine = create_async_engine(os.environ["QUOTES_TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(quotes, "session_factory", factory)
    minute = datetime(2026, 11, 26, 1, 0, tzinfo=UTC)

    def tick(second, price, volume):
        return dict(
            symbol="2330.TW",
            price=price,
            total_volume=volume,
            trade_time=minute.replace(second=second).isoformat(),
        )

    try:
        buffer = quotes.MinuteBuffer()
        buffer.add(tick(1, 100, 10))
        buffer.add(tick(2, 105, 15))
        await buffer.flush()
        # A collector restart in the same minute preserves the original open.
        restarted = quotes.MinuteBuffer()
        restarted.add(tick(3, 102, 20))
        flushing = asyncio.create_task(restarted.flush())
        await asyncio.sleep(0)
        restarted.add(tick(4, 103, 21))
        await flushing
        assert restarted.rows  # A callback during the await stays dirty for the next write.
        await restarted.flush()
        async with factory() as session:
            row = await session.scalar(
                select(QuoteMinuteRow).where(
                    QuoteMinuteRow.symbol == "2330.TW", QuoteMinuteRow.minute == minute
                )
            )
            assert (row.open, row.high, row.low, row.close) == (100, 105, 100, 103)
            assert row.last_total_volume - row.first_total_volume == 11
            assert row.is_partial
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    not (os.getenv("QUOTES_TEST_REDIS_URL") and os.getenv("QUOTES_TEST_DATABASE_URL")),
    reason="isolated Redis and Postgres required",
)
async def test_collector_end_to_end_with_fake_upstream(monkeypatch):
    from quant_signal.infrastructure.providers import shioaji_feed

    engine = create_async_engine(os.environ["QUOTES_TEST_DATABASE_URL"])
    monkeypatch.setattr(quotes, "session_factory", async_sessionmaker(engine))
    redis = Redis.from_url(os.environ["QUOTES_TEST_REDIS_URL"], decode_responses=True)
    subscriptions = []
    stamp = datetime(2026, 11, 26, 1, 2, tzinfo=UTC)

    class Feed:
        def __init__(self, settings, callback, disconnected):
            self.callback = callback

        def start(self):
            pass

        def subscribe(self, symbol):
            subscriptions.append(symbol)
            for second, price in [(1, 100), (2, 105), (3, 102)]:
                trade = stamp.replace(second=second)
                self.callback(
                    dict(
                        symbol=symbol,
                        price=price,
                        change_pct=0.025,
                        total_volume=second,
                        trade_time=trade.isoformat(),
                        trade_epoch=trade.timestamp(),
                        received_at=datetime.now(UTC).isoformat(),
                    )
                )

        def unsubscribe(self, symbol):
            pass

        def close(self):
            pass

    monkeypatch.setattr(shioaji_feed, "ShioajiFeed", Feed)
    stop = asyncio.Event()
    task = None
    try:
        await redis.set(LOCK, "test-owner", ex=30)
        await redis.zadd(DEMAND, {"2330.TW": time.time() + 60})
        task = asyncio.create_task(quotes.collect(redis, Settings(), "test-owner", stop))
        async with asyncio.timeout(5):
            while True:
                raw = await redis.get(PREFIX + "2330.TW")
                if (
                    raw
                    and json.loads(raw).get("trade_epoch") == stamp.replace(second=3).timestamp()
                ):
                    break
                await asyncio.sleep(0.05)
        stop.set()
        await task
        assert subscriptions == ["2330.TW"]
        async with async_sessionmaker(engine)() as session:
            bar = await session.scalar(
                select(QuoteMinuteRow).where(
                    QuoteMinuteRow.symbol == "2330.TW", QuoteMinuteRow.minute == stamp
                )
            )
            assert (bar.open, bar.high, bar.close) == (100, 105, 102)
    finally:
        stop.set()
        if task and not task.done():
            await task
        await redis.delete(LOCK, DEMAND, HEALTH, PREFIX + "2330.TW")
        await redis.aclose()
        await engine.dispose()
