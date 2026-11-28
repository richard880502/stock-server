"""A small in-process LRU cache for expensive whole-market results.

Keys carry a *data version* (see ``QuantRepository.market_data_version``), so a sync
that writes rows makes the old entries unreachable; the bound and the TTL then retire
them. Concurrent misses for one key compute once: later callers wait on a per-key lock
and find the entry the first one stored.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import Any


class ResultCache:
    def __init__(
        self,
        max_entries: int = 32,
        *,
        ttl_seconds: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._locks: dict[Hashable, tuple[asyncio.Lock, int]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: Hashable) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if self._clock() - stored_at > self.ttl_seconds:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def put(self, key: Hashable, value: Any) -> None:
        self._entries[key] = (self._clock(), value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    async def get_or_compute(self, key: Hashable, compute: Callable[[], Awaitable[Any]]) -> Any:
        value = self.get(key)
        if value is not None:
            return value
        lock, users = self._locks.get(key) or (asyncio.Lock(), 0)
        self._locks[key] = (lock, users + 1)
        try:
            async with lock:
                value = self.get(key)
                if value is None:
                    value = await compute()
                    self.put(key, value)
                return value
        finally:
            lock, users = self._locks[key]
            if users <= 1:
                del self._locks[key]
            else:
                self._locks[key] = (lock, users - 1)
