from __future__ import annotations

from datetime import date, timedelta

import httpx

from quant_signal.infrastructure.providers.cache import ReportCache
from quant_signal.infrastructure.providers.twse import TwseProvider


def _provider(counter: list[str]) -> TwseProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        counter.append(request.url.path)
        return httpx.Response(200, json={"stat": "OK", "data": []})

    client = httpx.AsyncClient(
        base_url="https://www.twse.com.tw", transport=httpx.MockTransport(handler)
    )
    return TwseProvider(client=client, request_interval_seconds=0)


async def test_settled_market_wide_report_is_fetched_once_for_many_symbols() -> None:
    calls: list[str] = []
    provider = _provider(calls)
    past = date.today() - timedelta(days=10)

    for symbol in ("2330.TW", "2317.TW", "2454.TW"):
        await provider.fetch_institutional_flow(symbol, past, include_holding_ratio=False)

    assert calls == ["/rwd/zh/fund/T86"]


async def test_todays_report_is_never_cached() -> None:
    calls: list[str] = []
    provider = _provider(calls)

    for symbol in ("2330.TW", "2317.TW"):
        await provider.fetch_institutional_flow(symbol, date.today(), include_holding_ratio=False)

    assert len(calls) == 2


def test_cache_is_bounded_and_least_recently_used() -> None:
    cache = ReportCache(max_entries=2)
    cache.put("/a", {}, {"n": 1})
    cache.put("/b", {}, {"n": 2})
    assert cache.get("/a", {}) == {"n": 1}  # refreshes /a
    cache.put("/c", {}, {"n": 3})

    assert cache.get("/b", {}) is None
    assert cache.get("/a", {}) == {"n": 1}
    assert cache.get("/c", {}) == {"n": 3}
