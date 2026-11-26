"""Single elected Shioaji collector. SDK callbacks enqueue; only this worker writes bars."""

import asyncio
import json
import logging
import signal
import time
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from redis.asyncio import Redis

from quant_signal.application.quote_cache import (
    DEMAND,
    HEALTH,
    LOCK,
    PREFIX,
    is_futures,
    volume_unit,
)
from quant_signal.infrastructure.db.database import session_factory
from quant_signal.infrastructure.db.prices import (
    LatestQuote,
    MinuteBar,
    store_latest,
    store_minutes,
)
from quant_signal.settings import get_settings

log = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
# Log in again before each futures session so the continuous near-month contract
# (TXFR1) follows the monthly roll: contracts are downloaded at login.
SESSION_REFRESH = ((8, 30), (14, 55))
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
    """Builds live 1-minute bars and the latest quote per symbol from ticks.

    Volume per minute is the change in the day's running total, starting just before
    the minute's first trade. Out-of-order ticks are dropped, so ``close`` is always
    the newest trade. Pending rows are written by ``flush()``.

    Each write sends only the volume added since this process last wrote that minute
    (``sent`` tracks it) and storage adds it on. A collector restarted mid-minute
    therefore continues the count instead of replacing it with a smaller one.
    """

    def __init__(self):
        self.rows = {}
        self.last = {}
        self.current = {}
        self.latest: dict[str, LatestQuote] = {}

    def add(self, tick):
        stamp = datetime.fromisoformat(tick["trade_time"])
        symbol = tick["symbol"]
        if stamp.tzinfo is None or stamp < self.last.get(symbol, stamp):
            return
        self.last[symbol] = stamp
        minute = stamp.replace(second=0, microsecond=0)
        key = (symbol, minute)
        price = tick["price"]
        total = tick["total_volume"]
        # total_volume already includes this trade; the minute starts just before it.
        base = max(0, total - max(0, int(tick.get("volume") or 0)))
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
                base=base,
                total=total,
                sent=base,
            )
        else:
            row.update(
                high=max(row["high"], price),
                low=min(row["low"], price),
                close=price,
                total=max(row["total"], total),
            )
        self.current[symbol] = self.rows[key]
        self.latest[symbol] = LatestQuote(
            symbol=symbol,
            price=price,
            reference=tick.get("reference"),
            open=tick.get("day_open"),
            high=tick.get("day_high"),
            low=tick.get("day_low"),
            volume=total,
            amount=tick.get("total_amount"),
            trade_time=stamp,
            source="tick",
            updated_at=datetime.now(TAIPEI),
        )

    def candle(self, symbol):
        row = self.current[symbol]
        unit = volume_unit(symbol)
        # Stocks: lots -> shares. Futures: contracts stay contracts.
        multiplier = 1 if unit == "contract" else 1000
        return {
            "time": int(row["minute"].timestamp()),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": max(0, row["total"] - row["base"]) * multiplier,
            "volume_unit": "contract" if unit == "contract" else "share",
            "is_partial": True,
        }

    @staticmethod
    def bar(row, *, pending: bool = False) -> MinuteBar:
        """The minute as seen by this process, or (``pending``) only what is unsent."""
        volume = row["total"] - (row["sent"] if pending else row["base"])
        return MinuteBar(
            symbol=row["symbol"],
            minute=row["minute"],
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=max(0, volume),
            official=False,
        )

    async def flush(self):
        if not self.rows and not self.latest:
            return
        # Called in the same event loop as add(). Failed writes remain for retry.
        rows = [row.copy() for row in list(self.rows.values())[:2000]]
        latest = list(self.latest.values())
        async with session_factory() as session:
            await store_minutes(session, [self.bar(r, pending=True) for r in rows], live=True)
            await store_latest(session, latest)
            await session.commit()
        for row in rows:
            key = (row["symbol"], row["minute"])
            current = self.rows.get(key)
            if current is None:
                continue
            current["sent"] = max(current["sent"], row["total"])
            # Ticks that arrived during the database await stay pending.
            if current == {**row, "sent": current["sent"]}:
                self.rows.pop(key, None)
        for quote in latest:
            if self.latest.get(quote.symbol) is quote:
                self.latest.pop(quote.symbol, None)


def last_session_refresh(now):
    """The most recent scheduled re-login time at or before ``now`` (Taipei)."""
    local = now.astimezone(TAIPEI)
    candidates = [
        datetime.combine(day, datetime.min.time(), TAIPEI).replace(hour=hour, minute=minute)
        for day in (local.date() - timedelta(days=1), local.date())
        for hour, minute in SESSION_REFRESH
    ]
    return max(moment for moment in candidates if moment <= local)


async def collect(redis, settings, owner, stop, health=None):
    from quant_signal.infrastructure.providers.shioaji_budget import ShioajiBudget
    from quant_signal.infrastructure.providers.shioaji_feed import ShioajiFeed
    from quant_signal.workers.market_snapshots import MarketSnapshots, UsageMonitor
    from quant_signal.workers.minute_backfill import MinuteBackfiller, market_universe

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
        budget=ShioajiBudget.from_settings(redis, settings),
    )
    bars = MinuteBuffer()
    subscribed = set()
    rejected = {}
    renewed = flushed = close_checked = snapshot_checked = 0.0
    connected = False
    connected_at = datetime.now(TAIPEI)
    usage = UsageMonitor(feed.usage, redis, settings.quotes_usage_reserve_mb * 1024 * 1024)

    async def publish(event):
        await redis.eval(
            PUBLISH, 3, LOCK, PREFIX + event["symbol"], PREFIX + "updates", owner, json.dumps(event)
        )

    snapshots = MarketSnapshots(
        redis, settings, feed.snapshot_quotes, market_universe, usage, publish
    )
    seeds: set[asyncio.Task] = set()
    backfiller = None
    if settings.quotes_backfill_enabled:
        backfiller = MinuteBackfiller(redis, settings, feed.minute_bars, usage_ok=usage.ok)
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
        # One sequential consumer: history queries never run concurrently.
        history = asyncio.create_task(backfiller.run()) if backfiller else None
        try:
            await asyncio.to_thread(feed.start)
            connected = True
            connected_at = datetime.now(TAIPEI)
            if health:
                health.connected = True
            while not stop.is_set() and not disconnected.is_set():
                if any(is_futures(s) for s in subscribed) and connected_at < last_session_refresh(
                    datetime.now(TAIPEI)
                ):
                    log.info("Refreshing the Shioaji session before the futures session")
                    break
                if backfiller and time.monotonic() - close_checked > 60:
                    await backfiller.check_after_close(subscribed)
                    close_checked = time.monotonic()
                if time.monotonic() - snapshot_checked > 30:
                    await snapshots.tick()
                    snapshot_checked = time.monotonic()
                if time.monotonic() - renewed > 5:
                    desired = set(await redis.zrangebyscore(DEMAND, time.time(), "+inf"))
                    desired = set(sorted(desired)[: settings.quotes_max_symbols])
                    for symbol in subscribed - desired:
                        await asyncio.to_thread(feed.unsubscribe, symbol)
                    for symbol in desired - subscribed:
                        if rejected.get(symbol, 0) > time.monotonic():
                            continue
                        try:
                            subscribed_at = datetime.now(TAIPEI)
                            await asyncio.to_thread(feed.subscribe, symbol)
                            subscribed.add(symbol)
                        except (ValueError, LookupError):
                            rejected[symbol] = time.monotonic() + 60
                            continue
                        if backfiller:
                            await backfiller.on_subscribed(symbol, subscribed_at)
                        # Today's price at once, even if the next trade is far away.
                        seed = asyncio.create_task(snapshots.seed(symbol))
                        seeds.add(seed)
                        seed.add_done_callback(seeds.discard)
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
            tasks = [task for task in (beat, history, *seeds) if task]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await snapshots.close()
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
