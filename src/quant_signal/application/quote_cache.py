"""Shared quote cache and bounded, expiring subscription demand across API replicas."""

import asyncio
import json
import re
import time
from contextlib import suppress

from redis.asyncio import Redis

PREFIX = "quant:quotes:"
DEMAND = PREFIX + "demand"
HEALTH = PREFIX + "health"
LOCK = PREFIX + "leader"
SYMBOL = re.compile(r"^[0-9]{4,6}[A-Z]?\.TW(O)?$")

# Admission is atomic across API replicas. Each requested symbol holds a 60s lease.
ADMIT = """
local now = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
local extra = 0
for i = 3, #ARGV do
  if not redis.call('ZSCORE', KEYS[1], ARGV[i]) then extra = extra + 1 end
end
if redis.call('ZCARD', KEYS[1]) + extra > tonumber(ARGV[2]) then return 0 end
for i = 3, #ARGV do redis.call('ZADD', KEYS[1], now + 60, ARGV[i]) end
return 1
"""

VIEWER_ADMIT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 0 end
redis.call('ZADD', KEYS[1], tonumber(ARGV[1]) + 310, ARGV[3])
redis.call('EXPIRE', KEYS[1], 330)
return 1
"""


def parse_symbols(raw: str) -> tuple[str, ...]:
    symbols = tuple(sorted(set(raw.upper().split(","))))
    if not 1 <= len(symbols) <= 25 or any(not SYMBOL.fullmatch(s) for s in symbols):
        raise ValueError("請提供最多 25 檔上市或上櫃股票代號")
    return symbols


class QuoteHub:
    """One Redis reader per API process, shared by every connected viewer."""

    def __init__(self, settings):
        self.settings = settings
        self.redis = Redis.from_url(
            settings.quotes_redis_url.get_secret_value(),
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        self.viewers: dict[str, int] = {}
        self.quotes: dict[str, dict] = {}
        self.status = "connecting"
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()

    async def join(self, symbols):
        async with self.lock:
            admitted = await self.redis.eval(
                ADMIT, 1, DEMAND, time.time(), self.settings.quotes_max_symbols, *symbols
            )
            if not admitted:
                raise ValueError("行情訂閱已達上限，請稍後再試")
            for symbol in symbols:
                self.viewers[symbol] = self.viewers.get(symbol, 0) + 1
            if self.task is None or self.task.done():
                self.task = asyncio.create_task(self.run())

    def leave(self, symbols):
        for symbol in symbols:
            count = self.viewers.get(symbol, 0) - 1
            if count <= 0:
                self.viewers.pop(symbol, None)
                self.quotes.pop(symbol, None)
            else:
                self.viewers[symbol] = count

    async def run(self):
        renewed = 0.0
        while self.viewers:
            symbols = tuple(self.viewers)
            try:
                if time.monotonic() - renewed > 15:
                    # Recreate demand after Redis restarts, with the same global cap.
                    admitted = await self.redis.eval(
                        ADMIT, 1, DEMAND, time.time(), self.settings.quotes_max_symbols, *symbols
                    )
                    if not admitted:
                        self.status = "unavailable"
                        await asyncio.sleep(1)
                        continue
                    renewed = time.monotonic()
                rows = await self.redis.mget([HEALTH, *(PREFIX + s for s in symbols)])
                self.status = rows[0] or "unavailable"
                self.quotes = {
                    s: json.loads(row)
                    for s, row in zip(symbols, rows[1:], strict=True)
                    if row and s in self.viewers
                }
            except Exception:
                self.status = "unavailable"
            await asyncio.sleep(1)

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        await self.redis.aclose()
