"""Single elected Shioaji collector. SDK callbacks enqueue; only this worker writes bars."""

import asyncio
import json
import logging
import signal
import time
from datetime import datetime
from uuid import uuid4

from redis.asyncio import Redis
from sqlalchemy import case, func
from sqlalchemy.dialects.postgresql import insert

from quant_signal.application.quote_cache import DEMAND, HEALTH, LOCK, PREFIX
from quant_signal.infrastructure.db.database import session_factory
from quant_signal.infrastructure.db.models import QuoteMinuteRow
from quant_signal.settings import get_settings

log = logging.getLogger(__name__)
RENEW = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
 return redis.call('EXPIRE', KEYS[1], 30)
end
return 0
"""
RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end
return 0
"""
# Fence every publish. Never let an expired collector overwrite a new leader.
PUBLISH = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
local previous = redis.call('GET', KEYS[2])
if previous then
 local last_time = cjson.decode(previous).trade_epoch
 if last_time and last_time > cjson.decode(ARGV[2]).trade_epoch then return 2 end
end
redis.call('SET', KEYS[2], ARGV[2], 'EX', 86400)
redis.call('PUBLISH', KEYS[3], ARGV[2])
return 1
"""


class MinuteBuffer:
    def __init__(self):
        self.rows = {}
        self.last = {}
        self.current = {}

    def add(self, tick):
        stamp = datetime.fromisoformat(tick["trade_time"])
        symbol = tick["symbol"]
        if stamp.tzinfo is None or stamp < self.last.get(symbol, stamp):
            return
        self.last[symbol] = stamp
        minute = stamp.replace(second=0, microsecond=0)
        key = (symbol, minute)
        price = tick["price"]
        volume = tick["total_volume"]
        row = self.rows.get(key)
        if row is None and self.current.get(symbol, {}).get("minute") == minute:
            row = self.current[symbol]
            self.rows[key] = row
        if row is None:
            if len(self.rows) >= 10000:
                self.rows.pop(next(iter(self.rows)))
                log.warning("Minute backlog full; oldest partial minute discarded")
            self.rows[key] = dict(
                symbol=symbol,
                minute=minute,
                open=price,
                high=price,
                low=price,
                close=price,
                first_time=stamp,
                last_time=stamp,
                first_total_volume=volume,
                last_total_volume=volume,
                is_partial=True,
            )
        else:
            row.update(
                high=max(row["high"], price),
                low=min(row["low"], price),
                close=price,
                last_time=stamp,
                last_total_volume=volume,
            )
        self.current[symbol] = self.rows[key]

    def candle(self, symbol):
        row = self.current[symbol]
        return {
            "time": int(row["minute"].timestamp()),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": max(0, row["last_total_volume"] - row["first_total_volume"]) * 1000,
            "is_partial": True,
        }

    async def flush(self):
        if not self.rows:
            return
        # Called in the same event loop as add(). Failed writes remain for retry.
        rows = [row.copy() for row in list(self.rows.values())[:500]]
        statement = insert(QuoteMinuteRow).values(rows)
        incoming = statement.excluded
        current = QuoteMinuteRow
        statement = statement.on_conflict_do_update(
            index_elements=["symbol", "minute"],
            set_={
                "high": func.greatest(current.high, incoming.high),
                "low": func.least(current.low, incoming.low),
                "close": case(
                    (incoming.last_time >= current.last_time, incoming.close), else_=current.close
                ),
                "last_time": func.greatest(current.last_time, incoming.last_time),
                "last_total_volume": func.greatest(
                    current.last_total_volume, incoming.last_total_volume
                ),
            },
        )
        async with session_factory() as session:
            await session.execute(statement)
            await session.commit()
        # Do not remove ticks received during the database await.
        for row in rows:
            key = (row["symbol"], row["minute"])
            if self.rows.get(key) == row:
                self.rows.pop(key, None)


async def collect(redis, settings, owner, stop, health=None):
    from quant_signal.infrastructure.providers.shioaji_feed import ShioajiFeed

    loop = asyncio.get_running_loop()
    queue = asyncio.Queue(maxsize=10000)
    disconnected = asyncio.Event()

    def enqueue(tick):
        if queue.full():
            disconnected.set()  # Reconnect and explicitly mark a gap.
        else:
            queue.put_nowait(tick)

    feed = ShioajiFeed(
        settings,
        lambda tick: loop.call_soon_threadsafe(enqueue, tick),
        lambda *args: loop.call_soon_threadsafe(disconnected.set),
    )
    bars = MinuteBuffer()
    subscribed = set()
    rejected = {}
    renewed = flushed = 0.0
    connected = False
    try:
        # Renew independently while SDK login blocks in a thread.
        async def heartbeat():
            while not stop.is_set():
                if not await redis.eval(RENEW, 1, LOCK, owner):
                    disconnected.set()
                    return
                await redis.set(HEALTH, "live" if connected else "connecting", ex=15)
                await asyncio.sleep(5)

        beat = asyncio.create_task(heartbeat())
        try:
            await asyncio.to_thread(feed.start)
            connected = True
            if health:
                health.connected = True
            while not stop.is_set() and not disconnected.is_set():
                if time.monotonic() - renewed > 5:
                    desired = set(await redis.zrangebyscore(DEMAND, time.time(), "+inf"))
                    desired = set(sorted(desired)[: settings.quotes_max_symbols])
                    for symbol in subscribed - desired:
                        await asyncio.to_thread(feed.unsubscribe, symbol)
                    for symbol in desired - subscribed:
                        if rejected.get(symbol, 0) > time.monotonic():
                            continue
                        try:
                            await asyncio.to_thread(feed.subscribe, symbol)
                            subscribed.add(symbol)
                        except (ValueError, LookupError):
                            rejected[symbol] = time.monotonic() + 60
                    subscribed.intersection_update(desired)
                    renewed = time.monotonic()
                try:
                    tick = await asyncio.wait_for(queue.get(), timeout=1)
                except TimeoutError:
                    tick = None
                if tick:
                    batch = [tick]
                    await asyncio.sleep(0.05)
                    while not queue.empty() and len(batch) < 500:
                        batch.append(queue.get_nowait())
                    latest = {}
                    for event in batch:
                        bars.add(event)
                        previous = latest.get(event["symbol"])
                        if previous is None or event["trade_epoch"] >= previous["trade_epoch"]:
                            latest[event["symbol"]] = event
                    pipeline = redis.pipeline(transaction=False)
                    for event in latest.values():
                        event["candle"] = bars.candle(event["symbol"])
                        pipeline.eval(
                            PUBLISH,
                            3,
                            LOCK,
                            PREFIX + event["symbol"],
                            PREFIX + "updates",
                            owner,
                            json.dumps(event),
                        )
                    outcomes = await pipeline.execute()
                    if 0 in outcomes:
                        break
                if time.monotonic() - flushed > 5:
                    try:
                        await bars.flush()
                    except Exception:
                        log.warning("Minute storage unavailable; retaining bounded backlog")
                    flushed = time.monotonic()
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
    finally:
        if health:
            health.connected = False
        await asyncio.to_thread(feed.close)
        if await redis.get(LOCK) == owner:
            await redis.set(HEALTH, "unavailable", ex=15)
            await bars.flush()


async def run():
    from quant_signal.workers.quote_health import QuoteHealth, serve

    settings = get_settings()
    if not settings.quotes_enabled:
        raise RuntimeError("Set QUOTES_ENABLED=true on the collector")
    if not (
        settings.shioaji_api_key.get_secret_value()
        and settings.shioaji_secret_key.get_secret_value()
    ):
        raise RuntimeError("Shioaji credentials are required on the collector")
    redis = Redis.from_url(
        settings.quotes_redis_url.get_secret_value(),
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    )
    stop = asyncio.Event()
    health = QuoteHealth()
    health_server = asyncio.create_task(serve(health, redis))

    async def pulse():
        while not stop.is_set():
            health.alive = time.monotonic()
            await asyncio.sleep(5)

    pulse_task = asyncio.create_task(pulse())
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    try:
        while not stop.is_set():
            if health_server.done():
                await health_server
                raise RuntimeError("Quote health server stopped")
            owner = str(uuid4())
            try:
                if await redis.set(LOCK, owner, nx=True, ex=30):
                    health.leader = True
                    try:
                        await collect(redis, settings, owner, stop, health)
                    finally:
                        health.leader = False
                        await redis.eval(RELEASE, 1, LOCK, owner)
            except Exception:
                # SDK exceptions can contain account data. Keep public logs generic.
                log.warning("Quote collector disconnected; retrying")
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass
    finally:
        health.connected = False
        for task in (health_server, pulse_task):
            task.cancel()
        await asyncio.gather(health_server, pulse_task, return_exceptions=True)
        await redis.aclose()


def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
