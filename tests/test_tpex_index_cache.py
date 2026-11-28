"""The TPEx index month cache must not go stale inside one long-lived worker process.

Recorded-shape fixtures (``/indexInfo/inx`` and ``/afterTrading/dailyQuotes``); no network.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from quant_signal.infrastructure.providers.tpex import INDEX_MONTH_REFRESH, TpexProvider

TAIPEI_EVENING = 10  # 10:00 UTC = 18:00 Taipei


def _at(day: date, minutes: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, TAIPEI_EVENING, tzinfo=UTC) + timedelta(
        minutes=minutes
    )


def _quote_row(code: str) -> list[str]:
    return [code, f"股{code}", "500", "+5", "496", "505", "495", "500", "1,000", "500,000"]


class FakeTpex:
    """Serves the index month as published so far and a quote report for every day asked."""

    def __init__(self, published: list[date]) -> None:
        self.published = published
        self.calls: Counter[str] = Counter()

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls[path] += 1
        requested = request.url.params.get("date", "")
        if path.endswith("/indexInfo/inx"):
            month = date.fromisoformat(requested.replace("/", "-")).replace(day=1)
            rows = [
                [day.strftime("%Y/%m/%d"), "290.00", "292.00", "288.00", "291.00", "1.00"]
                for day in self.published
                if day.replace(day=1) == month
            ]
            payload: dict[str, Any] = {
                "stat": "ok",
                "date": requested.replace("/", ""),
                "tables": [
                    {"fields": ["日期", "開市", "最高", "最低", "收市", "漲/跌"], "data": rows}
                ],
            }
        elif path.endswith("/afterTrading/dailyQuotes"):
            payload = {
                "stat": "ok",
                "date": requested.replace("/", ""),
                "tables": [{"data": [_quote_row("6488"), _quote_row("8299")]}],
            }
        else:  # flow / credit reports: published, nothing for these codes
            payload = {"stat": "ok", "tables": [{"data": []}]}
        return httpx.Response(200, json=payload)


def _provider(fake: FakeTpex, now: dict[str, datetime]) -> TpexProvider:
    client = httpx.AsyncClient(
        base_url="https://www.tpex.org.tw", transport=httpx.MockTransport(fake.handle)
    )
    return TpexProvider(client=client, request_interval_seconds=0, clock=lambda: now["value"])


INX = "/www/zh-tw/indexInfo/inx"
WED, THU, FRI = date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)


async def test_a_month_cached_early_still_answers_later_trading_days() -> None:
    fake = FakeTpex([date(2026, 11, 2), date(2026, 11, 3), WED])
    now = {"value": _at(WED)}
    provider = _provider(fake, now)

    assert (await provider.fetch_market_quotes(WED)) is not None
    # The next evening the exchange has published Thursday; the same process asks for it.
    fake.published.append(THU)
    now["value"] = _at(THU)
    thursday = await provider.fetch_market_quotes(THU)

    assert thursday is not None and thursday.index_close == 291.0
    assert fake.calls[INX] == 2
    stock_rows = await provider.fetch_market_stock_day(THU)
    assert {row.symbol for row in stock_rows} == {"6488.TWO", "8299.TWO"}
    assert fake.calls[INX] == 2  # cached now


async def test_a_genuinely_absent_date_does_not_refetch_on_every_call() -> None:
    fake = FakeTpex([date(2026, 11, 2), date(2026, 11, 3), WED])
    now = {"value": _at(FRI)}  # Thursday and Friday are not in the table (e.g. holidays)
    provider = _provider(fake, now)

    for _ in range(3):
        assert await provider.fetch_market_quotes(THU) is None
        assert await provider.fetch_market_quotes(FRI) is None
    assert fake.calls[INX] == 1

    now["value"] = _at(FRI, minutes=int(INDEX_MONTH_REFRESH.total_seconds() // 60))
    assert await provider.fetch_market_quotes(THU) is None
    assert await provider.fetch_market_quotes(FRI) is None
    assert fake.calls[INX] == 2  # one refetch per window, not one per date or call


async def test_future_dates_and_complete_past_months_are_not_refetched() -> None:
    fake = FakeTpex([date(2026, 10, 30), WED])
    now = {"value": _at(WED)}
    provider = _provider(fake, now)

    assert await provider.fetch_market_quotes(date(2026, 10, 30)) is not None
    assert await provider.fetch_market_quotes(WED) is not None
    calls = fake.calls[INX]
    now["value"] += timedelta(days=1)
    assert await provider.fetch_market_quotes(date(2026, 11, 20)) is None  # future
    assert await provider.fetch_market_quotes(date(2026, 10, 29)) is None  # October is complete
    assert fake.calls[INX] == calls


async def test_stock_rows_do_not_depend_on_the_index_close() -> None:
    fake = FakeTpex([date(2026, 11, 2)])  # the index month lags behind the quote report
    now = {"value": _at(WED)}
    provider = _provider(fake, now)
    await provider.fetch_market_quotes(date(2026, 11, 2))

    rows = await provider.fetch_market_stock_day(WED)

    assert [row.trading_date for row in rows] == [WED, WED]
    # The market-level observation still requires an index close.
    assert await provider.fetch_market_day(WED) is None


async def test_a_report_for_another_date_is_not_stored_under_the_requested_one() -> None:
    fake = FakeTpex([WED])
    now = {"value": _at(THU)}
    provider = _provider(fake, now)
    original = fake.handle

    def previous_session(request: httpx.Request) -> httpx.Response:
        response = original(request)
        if request.url.path.endswith("/dailyQuotes"):
            payload = response.json()
            payload["date"] = "20261104"  # a fallback to the last trading day
            return httpx.Response(200, json=payload)
        return response

    provider.client = httpx.AsyncClient(
        base_url="https://www.tpex.org.tw", transport=httpx.MockTransport(previous_session)
    )
    assert await provider.fetch_market_stock_day(THU) == []


async def test_quote_range_refreshes_a_stale_month_once() -> None:
    fake = FakeTpex([date(2026, 11, 2), date(2026, 11, 3)])
    now = {"value": _at(date(2026, 11, 3))}
    provider = _provider(fake, now)
    await provider.fetch_market_quotes(date(2026, 11, 3))

    fake.published.extend([WED, THU])
    now["value"] = _at(THU)
    result = await provider.fetch_market_quotes_range([WED, THU, FRI])

    assert result[WED] is not None and result[THU] is not None and result[FRI] is None
    assert fake.calls[INX] == 2
