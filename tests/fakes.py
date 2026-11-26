"""Shared fakes: a provider that never touches the network and records its calls."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from quant_signal.domain.models import Bar, CorporateAction, InstitutionalFlow


def _weekdays(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


class FakeProvider:
    """Serves synthetic bars for any requested range and records every call."""

    def __init__(self, actions: list[CorporateAction] | None = None) -> None:
        self.instrument_names: dict[str, str] = {}
        self.actions = actions or []
        self.bar_calls: list[tuple[date, date]] = []
        self.action_calls: list[tuple[date, date]] = []
        self.flow_calls: list[tuple[date, date]] = []
        self.empty_before: date | None = None

    async def fetch_symbol_bars(self, symbol: str, *, start: date, end: date) -> list[Bar]:
        self.bar_calls.append((start, end))
        self.instrument_names[symbol] = "TSMC"
        days = _weekdays(start, end)
        if self.empty_before:
            days = [day for day in days if day >= self.empty_before]
        return [
            Bar(
                symbol=symbol,
                trading_date=day,
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1_000,
                available_at=datetime.combine(day, time(6), tzinfo=UTC),
                source="twse_official",
            )
            for day in days
        ]

    async def fetch_index_bars(self, *, start: date, end: date) -> list[Bar]:
        return await self.fetch_symbol_bars("^TWII", start=start, end=end)

    async def fetch_corporate_actions(
        self, symbol: str, *, start: date, end: date
    ) -> list[CorporateAction]:
        self.action_calls.append((start, end))
        return [a for a in self.actions if start <= a.ex_date <= end]

    async def fetch_institutional_flows(
        self, symbol: str, *, start: date, end: date, concurrency: int = 4
    ) -> list[InstitutionalFlow]:
        self.flow_calls.append((start, end))
        return [
            InstitutionalFlow(
                symbol=symbol,
                trading_date=day,
                foreign_buy=2,
                foreign_sell=1,
                foreign_net=1,
                investment_trust_buy=0,
                investment_trust_sell=0,
                investment_trust_net=0,
                dealer_buy=0,
                dealer_sell=0,
                dealer_net=0,
                total_net=1,
                available_at=datetime.combine(day, time(11), tzinfo=UTC),
                source="twse_official",
            )
            for day in _weekdays(start, end)
        ]


class FakeMarketProvider:
    """Whole-market snapshot provider: trades on weekdays except listed holidays."""

    def __init__(self, holidays: set[date] | None = None) -> None:
        self.holidays = holidays or set()
        self.calls: list[date] = []

    async def fetch_market_stock_day(self, trading_date: date):
        from quant_signal.domain.models import MarketStockDay

        self.calls.append(trading_date)
        if trading_date.weekday() >= 5 or trading_date in self.holidays:
            return []
        return [
            MarketStockDay(
                symbol=f"{code}.TW",
                name=f"stock{code}",
                market="TW",
                trading_date=trading_date,
                open=10,
                high=11,
                low=9,
                close=10,
                volume=1000,
                turnover_value=10_000,
                available_at=datetime.combine(trading_date, time(10), tzinfo=UTC),
                source="twse_official",
            )
            for code in ("1101", "1102")
        ]


class FakeRedis:
    """In-memory subset of redis.asyncio used by the quote budget and backfill."""

    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now
        self.values: dict[str, str] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.sets: dict[str, set[str]] = {}
        self.ttl: dict[str, float] = {}
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis down")

    async def time(self):
        self._check()
        return int(self.now), int(round((self.now % 1) * 1e6))

    async def get(self, key):
        self._check()
        return self.values.get(key)

    async def set(self, key, value, nx=False, ex=None):
        self._check()
        if nx and key in self.values:
            return None
        self.values[key] = str(value)
        if ex:
            self.ttl[key] = ex
        return True

    async def delete(self, *keys):
        self._check()
        return sum(self.values.pop(key, None) is not None for key in keys)

    async def incr(self, key):
        self._check()
        self.values[key] = str(int(self.values.get(key, 0)) + 1)
        return int(self.values[key])

    async def expire(self, key, seconds):
        self._check()
        self.ttl[key] = seconds
        return True

    async def zrem(self, key, member):
        self._check()
        return self.zsets.get(key, {}).pop(member, None) is not None

    async def zrange(self, key, start, stop, withscores=False):
        self._check()
        items = sorted(self.zsets.get(key, {}).items(), key=lambda item: item[1])
        items = items[start : None if stop == -1 else stop + 1]
        return items if withscores else [member for member, _ in items]

    async def sadd(self, key, *members):
        self._check()
        self.sets.setdefault(key, set()).update(members)
        return len(members)

    async def smembers(self, key):
        self._check()
        return set(self.sets.get(key, set()))

    def pipeline(self, transaction=True):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.ops: list = []

    def zremrangebyscore(self, key, low, high):
        def op():
            zset = self.redis.zsets.setdefault(key, {})
            limit = float(high)
            for member in [m for m, score in zset.items() if score <= limit]:
                del zset[member]
            return 0

        self.ops.append(op)

    def zadd(self, key, mapping):
        self.ops.append(lambda: self.redis.zsets.setdefault(key, {}).update(mapping) or 1)

    def zcard(self, key):
        self.ops.append(lambda: len(self.redis.zsets.get(key, {})))

    def expire(self, key, seconds):
        self.ops.append(lambda: True)

    async def execute(self):
        self.redis._check()
        return [op() for op in self.ops]
