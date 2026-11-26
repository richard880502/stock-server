from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from quant_signal.application import official_events as sched
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.infrastructure.providers import official_events as src

FIXTURES = Path(__file__).parent / "fixtures" / "official"

URL_TO_FIXTURE = {
    src.TWSE_MATERIAL_URL: "twse_material",
    src.TPEX_MATERIAL_URL: "tpex_material",
    src.TWSE_PUNISH_URL: "twse_punish",
    src.TPEX_DISPOSAL_URL: "tpex_disposal",
    src.TWSE_NOTICE_URL: "twse_notice_empty",
    src.TWSE_NOTETRANS_URL: "twse_notetrans",
    src.TPEX_WARNING_URL: "tpex_warning",
    src.TPEX_WARNING_NOTE_URL: "tpex_warning_note",
    src.TWSE_EXRIGHTS_URL: "twse_exrights",
    src.TPEX_EXRIGHTS_URL: "tpex_exrights",
    src.TWSE_REVENUE_URL: "twse_revenue",
    src.TPEX_REVENUE_URL: "tpex_revenue",
}


class FakeExchange:
    """Serves recorded payloads through httpx's mock transport; counts every request."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.requests: Counter[str] = Counter()
        self.fail = fail or set()
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests[url] += 1
        if url in self.fail:
            return httpx.Response(500)
        name = URL_TO_FIXTURE[url]
        return httpx.Response(200, json=json.loads((FIXTURES / f"{name}.json").read_text()))


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(sched, "MIN_REVENUE_ROWS", 1)  # the fixtures hold a handful of rows


def taipei(day: int, hour: int, minute: int = 0, *, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC) - timedelta(hours=8)


# 2026-11-26 is a Thursday, 11-28 a Saturday.
THU_10 = taipei(26, 10)


def test_slots() -> None:
    assert sched.twice_daily_slot(taipei(26, 17, 1)) == "2026-11-26-pm"
    assert sched.twice_daily_slot(taipei(26, 8)) == "2026-11-26-am"
    assert sched.twice_daily_slot(taipei(26, 16, 59)) == "2026-11-26-am"
    assert sched.twice_daily_slot(taipei(26, 7, 59)) is None
    assert sched.twice_daily_slot(taipei(28, 18)) is None  # Saturday

    assert sched.material_slot(taipei(26, 9, 29)) == "2026-11-26T09:00"
    assert sched.material_slot(taipei(26, 9, 30)) == "2026-11-26T09:30"
    assert sched.material_slot(taipei(26, 7, 0)) == "2026-11-26T07:00"
    assert sched.material_slot(taipei(26, 22, 0)) == "2026-11-26T22:00"
    assert sched.material_slot(taipei(26, 22, 1)) is None
    assert sched.material_slot(taipei(26, 6, 59)) is None
    assert sched.material_slot(taipei(28, 12)) is None
    assert sched.material_slot(taipei(26, 9, 40), 10) == "2026-11-26T09:40"

    assert sched.revenue_slot(taipei(10, 3, month=11)) == "2026-11-10"  # daily in days 1-15
    assert sched.revenue_slot(taipei(15, 23, month=11)) == "2026-11-15"
    assert sched.revenue_slot(taipei(26, 10)) == sched.revenue_slot(taipei(27, 10)) == "2026-W48"
    assert sched.revenue_slot(taipei(16, 10, month=11)) == "2026-W47"


async def test_first_pass_fetches_everything_due_and_stores_it() -> None:
    repo, exchange = MemoryQuantRepository(), FakeExchange()
    written = await sched.refresh_official_data(
        repo, exchange, exchange, now=THU_10, request_interval=0
    )

    assert set(written) == {"risk_flags", "ex_rights", "material", "monthly_revenue"}
    assert len(repo.risk_flags) == written["risk_flags"] > 0
    assert {e.kind for e in repo.market_events} == {"material", "ex_rights"}
    assert len(repo.revenue_rows) == 6  # 4 TWSE (912000 dropped) + 2 TPEx
    assert all(r.available_at == THU_10 for r in repo.revenue_rows)
    assert len(exchange.requests) == 12 and set(exchange.requests.values()) == {1}
    for dataset, period in [
        ("risk_flags", "2026-11-26-am"),
        ("ex_rights", "2026-11-26-am"),
        ("material", "2026-11-26T10:00"),
        ("monthly_revenue", "2026-W48"),
    ]:
        done = await repo.list_fetch_coverage(sched.SOURCE, dataset, "*", [period])
        assert done[period].status == "ok"


async def test_restart_and_same_slot_do_not_refetch() -> None:
    repo, exchange = MemoryQuantRepository(), FakeExchange()
    await sched.refresh_official_data(repo, exchange, exchange, now=THU_10, request_interval=0)
    before = sum(exchange.requests.values())

    # A fresh process (empty in-memory attempts) in the same slots, 20 minutes later.
    again = await sched.refresh_official_data(
        repo,
        exchange,
        exchange,
        now=THU_10 + timedelta(minutes=20),
        attempts={},
        request_interval=0,
    )
    assert again == {}  # 10:20 is still the 10:00 window, already recorded
    assert sum(exchange.requests.values()) == before


async def test_next_half_hour_fetches_only_material() -> None:
    repo, exchange = MemoryQuantRepository(), FakeExchange()
    await sched.refresh_official_data(repo, exchange, exchange, now=THU_10, request_interval=0)
    exchange.requests.clear()

    written = await sched.refresh_official_data(
        repo, exchange, exchange, now=THU_10 + timedelta(minutes=30), request_interval=0
    )
    assert set(written) == {"material"} and written["material"] == 0  # same announcements
    assert set(exchange.requests) == {src.TWSE_MATERIAL_URL, src.TPEX_MATERIAL_URL}


async def test_evening_slot_reruns_risk_flags_and_ex_rights() -> None:
    repo, exchange = MemoryQuantRepository(), FakeExchange()
    await sched.refresh_official_data(repo, exchange, exchange, now=THU_10, request_interval=0)
    exchange.requests.clear()
    written = await sched.refresh_official_data(
        repo, exchange, exchange, now=taipei(26, 17, 5), request_interval=0
    )
    assert {"risk_flags", "ex_rights"} <= set(written)
    assert "monthly_revenue" not in written  # weekly slot already done


async def test_weekend_and_night_run_only_revenue() -> None:
    repo, exchange = MemoryQuantRepository(), FakeExchange()
    written = await sched.refresh_official_data(
        repo, exchange, exchange, now=taipei(28, 12), request_interval=0
    )
    assert set(written) == {"monthly_revenue"}
    exchange.requests.clear()
    night = await sched.refresh_official_data(
        repo, exchange, exchange, now=taipei(27, 3), request_interval=0
    )
    assert night == {}  # Friday 03:00: nothing due, revenue's weekly slot is done


async def test_revenue_is_daily_early_in_the_month() -> None:
    repo, exchange = MemoryQuantRepository(), FakeExchange()
    await sched.refresh_official_data(
        repo, exchange, exchange, now=taipei(8, 6, month=12), request_interval=0
    )
    written = await sched.refresh_official_data(
        repo, exchange, exchange, now=taipei(9, 6, month=12), request_interval=0
    )
    assert "monthly_revenue" in written  # a new day, a new daily slot


async def test_failed_endpoint_is_retried_later_and_never_raises() -> None:
    repo = MemoryQuantRepository()
    exchange = FakeExchange(fail={src.TPEX_MATERIAL_URL})
    attempts: dict[str, datetime] = {}
    kwargs = {"attempts": attempts, "request_interval": 0, "fetch_attempts": 1}

    written = await sched.refresh_official_data(repo, exchange, exchange, now=THU_10, **kwargs)
    assert "material" in written  # the TWSE half was still stored
    assert {e.symbol for e in repo.market_events if e.kind == "material"} >= {"2317.TW"}
    assert not any(e.symbol.endswith(".TWO") and e.kind == "material" for e in repo.market_events)
    assert "material" in attempts
    assert (
        await repo.list_fetch_coverage(sched.SOURCE, "material", "*", ["2026-11-26T10:00"]) == {}
    )  # slot stays open
    assert set(written) >= {"risk_flags", "monthly_revenue"}  # siblings unaffected

    exchange.requests.clear()
    await sched.refresh_official_data(
        repo, exchange, exchange, now=THU_10 + timedelta(minutes=5), **kwargs
    )
    assert exchange.requests == Counter()  # inside the 10-minute back-off

    exchange.fail.clear()
    await sched.refresh_official_data(
        repo, exchange, exchange, now=THU_10 + timedelta(minutes=11), **kwargs
    )
    assert exchange.requests[src.TPEX_MATERIAL_URL] == 1
    assert "material" not in attempts
    assert any(e.symbol.endswith(".TWO") and e.kind == "material" for e in repo.market_events)


async def test_incomplete_revenue_payload_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(sched, "MIN_REVENUE_ROWS", 500)
    repo, exchange = MemoryQuantRepository(), FakeExchange()
    written = await sched.refresh_official_data(
        repo, exchange, exchange, now=THU_10, request_interval=0
    )
    assert "monthly_revenue" not in written and repo.revenue_rows == []
