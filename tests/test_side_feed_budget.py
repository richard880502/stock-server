"""A hanging exchange must never delay the price sync: first timeout ends a host for the
tick, every side feed has a wall-clock budget, and the price sync runs before them."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from quant_signal.application import fundamentals_data as fd
from quant_signal.application import holders_data as hd
from quant_signal.application import short_data as sd
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.settings import Settings
from quant_signal.workers import sync

TIMEOUT = 30.0


class HangingExchange:
    """Every request times out, like an exchange that accepts and never answers."""

    def __init__(self) -> None:
        self.hosts: list[str] = []
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        raise httpx.ReadTimeout("timed out", request=request)


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    """Record the back-off sleeps instead of waiting for them."""
    seen: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float, *args, **kwargs) -> None:
        seen.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return seen


def worst_case_seconds(requests: int, slept: list[float]) -> float:
    return requests * TIMEOUT + sum(slept)


async def test_fundamentals_gives_up_on_a_host_at_its_first_timeout(slept) -> None:
    twse, tpex, attempts = HangingExchange(), HangingExchange(), {}
    now = datetime(2026, 11, 28, 2, 0, tzinfo=UTC)  # Saturday 10:00 Taipei: every slot due
    written = await fd.refresh_fundamentals_data(
        MemoryQuantRepository(), twse, tpex, now=now, attempts=attempts, mops_interval=3.0
    )
    requests = len(twse.hosts) + len(tpex.hosts)
    # one failed request per host (valuation x2, OpenAPI x2, MOPS x1), not 12 URLs x 3 tries
    assert requests <= 6, (twse.hosts, tpex.hosts)
    assert written == {} or all(count == 0 for count in written.values())
    assert any(key.startswith("blocked:") for key in attempts)
    assert fd.FINANCIALS_FORWARD in attempts  # the slot is retried after the retry delay
    # the old code made 3 attempts x 12 URLs per exchange: ~27 minutes in the worst case
    assert worst_case_seconds(requests, slept) < 6 * TIMEOUT + 60
    # a second tick inside the retry delay sends nothing at all
    before = requests
    await fd.refresh_fundamentals_data(
        MemoryQuantRepository(),
        twse,
        tpex,
        now=now + timedelta(minutes=2),
        attempts=attempts,
        mops_interval=3.0,
    )
    assert len(twse.hosts) + len(tpex.hosts) == before


async def test_short_data_gives_up_on_a_host_at_its_first_timeout(slept) -> None:
    twse, tpex, attempts = HangingExchange(), HangingExchange(), {}
    now = datetime(2026, 11, 27, 14, 40, tzinfo=UTC)  # Friday 22:40 Taipei: every slot due
    await sd.refresh_short_data(MemoryQuantRepository(), twse, tpex, now=now, attempts=attempts)
    requests = len(twse.hosts) + len(tpex.hosts)
    assert requests <= 4, (twse.hosts, tpex.hosts)  # not 3 attempts x 3 days x 3 sources
    assert worst_case_seconds(requests, slept) < 4 * TIMEOUT + 30
    again = len(twse.hosts) + len(tpex.hosts)
    await sd.refresh_short_data(
        MemoryQuantRepository(), twse, tpex, now=now + timedelta(minutes=1), attempts=attempts
    )
    assert len(twse.hosts) + len(tpex.hosts) == again


async def test_holders_gives_up_on_a_host_at_its_first_timeout(slept) -> None:
    twse, tpex = HangingExchange(), HangingExchange()
    now = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)
    await hd.refresh_holders_data(MemoryQuantRepository(), twse, tpex, now=now, attempts={})
    assert len(twse.hosts) + len(tpex.hosts) <= 3
    assert sum(slept) < 10


# --- the worker tick --------------------------------------------------------------------------


class _Session:
    pass


class _Repo(MemoryQuantRepository):
    def __init__(self, session) -> None:
        super().__init__()
        self.session = session

    async def requeue_stale_sync_jobs(self, *, older_than_seconds: int) -> int:
        return 0


@pytest.fixture
def tick_env(monkeypatch):
    """Patch the worker's collaborators; returns the call log."""
    log: list[str] = []
    settings = Settings().model_copy(update={"side_feed_budget_seconds": 0.2})

    @asynccontextmanager
    async def fake_session_factory():
        yield _Session()

    async def noop(*args, **kwargs):
        return None

    async def enqueue(*args, **kwargs):
        log.append("enqueue")

    async def process(*args, **kwargs):
        log.append("process")
        return True

    def hanging(name: str):
        async def feed(*args, **kwargs):
            log.append(f"start {name}")
            await asyncio.sleep(3600)  # an exchange that never answers
            log.append(f"finished {name}")  # pragma: no cover - cancelled by the budget

        return feed

    monkeypatch.setattr(sync, "get_settings", lambda: settings)
    monkeypatch.setattr(sync, "session_factory", fake_session_factory)
    monkeypatch.setattr(sync, "PostgresQuantRepository", _Repo)
    monkeypatch.setattr(sync, "ensure_market_snapshots", noop)
    monkeypatch.setattr(sync, "enqueue_daily_refresh", enqueue)
    monkeypatch.setattr(sync, "process_next_job", process)
    for name in (
        "refresh_industries",
        "refresh_official_data",
        "refresh_holders_data",
        "refresh_short_data",
        "refresh_fundamentals_data",
        "refresh_derivatives_data",
    ):
        monkeypatch.setattr(sync, name, hanging(name))
    monkeypatch.setattr(sync, "_industry_attempt", {"at": datetime.min.replace(tzinfo=UTC)})
    return log


async def test_tick_returns_within_the_budgets_and_the_price_sync_still_runs(tick_env) -> None:
    log = tick_env
    started = time.monotonic()
    outcome = await asyncio.wait_for(sync.tick(object(), object(), object()), timeout=20)  # type: ignore[arg-type]
    elapsed = time.monotonic() - started
    assert outcome is True
    # six feeds that each hang are cancelled at 0.2 s apiece
    assert elapsed < 3, elapsed
    assert "process" in log and "enqueue" in log
    for name in (
        "refresh_industries",
        "refresh_official_data",
        "refresh_holders_data",
        "refresh_short_data",
        "refresh_fundamentals_data",
        "refresh_derivatives_data",
    ):
        assert f"start {name}" in log and f"finished {name}" not in log
    # the price sync is not queued behind the side feeds
    process_at = log.index("process")
    assert all(
        log.index(entry) > process_at
        for entry in log
        if entry.startswith("start ") and entry != "start refresh_industries"
    )


async def test_a_failing_price_job_does_not_starve_the_side_feeds(tick_env, monkeypatch) -> None:
    log = tick_env

    async def broken(*args, **kwargs):
        log.append("process")
        raise RuntimeError("job exploded")

    monkeypatch.setattr(sync, "process_next_job", broken)
    with pytest.raises(RuntimeError, match="job exploded"):
        await asyncio.wait_for(sync.tick(object(), object(), object()), timeout=20)  # type: ignore[arg-type]
    assert "start refresh_short_data" in log  # the side feeds still got their turn
