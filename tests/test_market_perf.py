"""Market page performance work: equivalence with the old code paths, caches, event loop.

* Rankings are aggregated by the store; ``_reference_ranking`` below is the previous
  in-Python implementation, kept as the oracle the new path must agree with.
* Sector rotation is compared to a capture of the previous implementation's output.
* The heavy handlers must not block the event loop.
"""

from __future__ import annotations

import asyncio
import json
import random
import threading
import time as clock
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from market_perf_fixture import (
    MONDAY_AFTER_CLOSE,
    MONDAY_IN_SESSION,
    build_rows,
    sessions,
)
from sector_golden_cases import capture

import quant_signal.application.market_sectors as sectors_module
from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.application.market_rankings import MarketRankingService
from quant_signal.application.market_sectors import SectorRotationService
from quant_signal.application.result_cache import ResultCache
from quant_signal.domain.models import MarketRankingRow, MarketStockDay
from quant_signal.domain.ranking import (
    REQUIRED_REPORT,
    SHARE_COLUMNS,
    ranking_raw_value,
)
from quant_signal.infrastructure.memory import MemoryQuantRepository

GOLDEN = Path(__file__).parent / "fixtures" / "sector_golden.json"
METRICS = (
    "foreign_net",
    "trust_net",
    "dealer_net",
    "total_net",
    "volume",
    "turnover",
    "change_pct",
    "margin_change",
    "short_change",
)


# ------------------------------------------------------------------ sector golden
async def test_sector_output_matches_the_golden_capture() -> None:
    """``sector_golden.json`` was captured from the implementation before the refactor."""
    expected = json.loads(GOLDEN.read_text())
    actual = json.loads(json.dumps(await capture(), ensure_ascii=False))

    assert actual.keys() == expected.keys()
    assert [name for name in expected if actual[name] != expected[name]] == []


# --------------------------------------------------------------------- rankings
async def _reference_ranking(
    repo: MemoryQuantRepository,
    metric: str,
    *,
    market: str,
    window: int,
    order: str,
    as_of: date | None,
) -> list[tuple[str, float, float]]:
    """The previous implementation: every row in Python (symbol, value, close)."""
    scope = None if market == "ALL" else market
    require = REQUIRED_REPORT.get(metric)
    dates = await repo.list_market_trading_dates(scope, as_of=as_of, limit=window, require=require)
    if not dates:
        return []
    rows = await repo.list_market_stock_days(dates, scope, as_of=as_of)
    latest = max(dates)
    by_symbol: dict[str, list[MarketStockDay]] = defaultdict(list)
    for row in rows:
        by_symbol[row.symbol].append(row)
    scored: list[tuple[float, list[MarketStockDay]]] = []
    for history in by_symbol.values():
        history.sort(key=lambda item: item.trading_date)
        if history[-1].trading_date != latest:
            continue
        value = _reference_value(metric, history)
        if value is not None:
            scored.append((value, history))
    scored.sort(key=lambda pair: pair[0], reverse=order == "desc")
    return [(h[-1].symbol, v, h[-1].close) for v, h in scored]


def _reference_value(metric: str, history: list[MarketStockDay]) -> float | None:
    if metric in SHARE_COLUMNS:
        known = [v for r in history if (v := getattr(r, metric)) is not None]
        return sum(known) / 1000 if known else None
    if metric in ("margin_change", "short_change"):
        known = [v for r in history if (v := getattr(r, metric)) is not None]
        return float(sum(known)) if known else None
    if metric == "volume":
        return sum(r.volume for r in history) / 1000
    if metric == "turnover":
        return sum(r.turnover_value for r in history)
    base = history[0].close - history[0].change
    return (history[-1].close / base - 1) * 100 if base > 0 else None


def _random_market(seed: int) -> list[MarketStockDay]:
    rng = random.Random(seed)
    rows = build_rows(stocks=45, count=14, seed=seed)
    days = sessions(14)
    out: list[MarketStockDay] = []
    for row in rows:
        update: dict[str, Any] = {}
        # TWO has not published its reports for the newest two sessions.
        if row.market == "TWO" and row.trading_date in days[-2:] and seed % 2:
            update = {
                "foreign_net": None,
                "trust_net": None,
                "dealer_net": None,
                "total_net": None,
                "margin_change": None,
                "short_change": None,
            }
        elif rng.random() < 0.1:  # an isolated hole in the reports
            update = {"short_change": None, "foreign_net": None}
        if rng.random() < 0.03:  # a ratio of zero closes and zero changes
            update["change"] = 0.0
        out.append(row.model_copy(update=update))
    return out


def _normalise(
    entries: list[tuple[str, float, float]], order: str
) -> list[tuple[str, float, float]]:
    # Ties have no defined order in the old path; compare them by symbol.
    sign = -1 if order == "desc" else 1
    return sorted(entries, key=lambda e: (sign * e[1], e[0]))


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
async def test_sql_ranking_contract_matches_the_in_python_reference(seed: int) -> None:
    repo = MemoryQuantRepository()
    await repo.upsert_market_stock_days(_random_market(seed))
    service = MarketRankingService(repo)
    checked = 0
    for metric in METRICS:
        for market in ("ALL", "TW", "TWO"):
            for window in (1, 3, 8):
                for order in ("desc", "asc"):
                    for as_of in (None, sessions(14)[-3]):
                        reference = _normalise(
                            await _reference_ranking(
                                repo, metric, market=market, window=window, order=order, as_of=as_of
                            ),
                            order,
                        )
                        result = await service.ranking(
                            metric,  # type: ignore[arg-type]
                            market=market,  # type: ignore[arg-type]
                            window=window,
                            order=order,  # type: ignore[arg-type]
                            limit=1000,
                            as_of=as_of,
                        )
                        actual = [(e.symbol, e.value, e.close) for e in result.entries]
                        assert [e.rank for e in result.entries] == list(range(1, len(actual) + 1))
                        assert actual == reference, (metric, market, window, order, as_of)
                        top = await service.ranking(
                            metric,  # type: ignore[arg-type]
                            market=market,  # type: ignore[arg-type]
                            window=window,
                            order=order,  # type: ignore[arg-type]
                            limit=5,
                            as_of=as_of,
                        )
                        assert [(e.symbol, e.value, e.close) for e in top.entries] == actual[:5]
                        checked += 1
    assert checked == 9 * 3 * 3 * 2 * 2


async def test_ranking_values_by_hand() -> None:
    """Three stocks, three sessions: every metric worked out on paper."""
    d1, d2, d3 = date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30)

    def row(symbol, day, close, change, foreign, volume, margin, market="TW"):
        return MarketStockDay(
            symbol=symbol,
            name=symbol,
            market=market,
            trading_date=day,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=volume,
            turnover_value=close * volume,
            change=change,
            foreign_net=foreign,
            total_net=foreign,
            margin_change=margin,
            available_at=datetime.combine(day, time(10), tzinfo=UTC),
            source="twse_official",
        )

    repo = MemoryQuantRepository()
    await repo.upsert_market_stock_days(
        [
            # A: close 100 -> 110 -> 121, reference of the first day 90.
            row("A.TW", d1, 100, 10, 1_000_000, 2_000_000, 5),
            row("A.TW", d2, 110, 10, 2_000_000, 3_000_000, -2),
            row("A.TW", d3, 121, 11, None, 1_000_000, None),
            # B: flat 50, sold 1.5 million shares, no data on d1 for credit.
            row("B.TW", d1, 50, 0, -500_000, 1_000_000, None),
            row("B.TW", d2, 50, 0, -500_000, 1_000_000, 7),
            row("B.TW", d3, 50, 0, -500_000, 1_000_000, 7),
            # C: stopped trading after d2, so it is never ranked.
            row("C.TW", d1, 10, 0, 9_000_000, 1_000, 1),
            row("C.TW", d2, 10, 0, 9_000_000, 1_000, 1),
        ]
    )
    service = MarketRankingService(repo)

    async def top(metric: str, **kw: Any) -> list[tuple[str, float]]:
        result = await service.ranking(metric, window=3, as_of=d3, **kw)  # type: ignore[arg-type]
        return [(e.symbol, e.value) for e in result.entries]

    # A report counts for a session once the market published it (B did on d3), and a
    # stock's own unknown day adds nothing: A = 1.0m + 2.0m shares, B = -0.5m x 3.
    assert await top("foreign_net") == [("A.TW", 3000.0), ("B.TW", -1500.0)]
    assert await top("volume") == [("A.TW", 6000.0), ("B.TW", 3000.0)]
    assert await top("turnover") == [
        ("A.TW", 100 * 2e6 + 110 * 3e6 + 121 * 1e6),
        ("B.TW", 50 * 3e6),
    ]
    # Window return over the first day's reference price (100 - 10 = 90): 121/90 - 1.
    change = await top("change_pct")
    assert [s for s, _ in change] == ["A.TW", "B.TW"]
    assert change[0][1] == pytest.approx((121 / 90 - 1) * 100)
    assert change[1][1] == pytest.approx(0.0)
    # Credit change sums what is known (A: 5 - 2 = 3 lots; B: 7 + 7 = 14), and a name
    # with nothing known in the window would be left out.
    assert await top("margin_change") == [("B.TW", 14.0), ("A.TW", 3.0)]
    assert await top("margin_change", order="asc", limit=1) == [("A.TW", 3.0)]


# ------------------------------------------------------------------ result cache
async def test_cache_is_lru_and_bounded() -> None:
    cache = ResultCache(max_entries=2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1  # a is now the freshest
    cache.put("c", 3)
    assert cache.get("b") is None and cache.get("a") == 1 and cache.get("c") == 3
    assert len(cache) == 2


async def test_cache_entries_expire() -> None:
    now = [0.0]
    cache = ResultCache(ttl_seconds=10, clock=lambda: now[0])
    cache.put("k", "v")
    now[0] = 9
    assert cache.get("k") == "v"
    now[0] = 11
    assert cache.get("k") is None and len(cache) == 0


async def test_concurrent_misses_compute_once() -> None:
    cache = ResultCache()
    calls = 0

    async def compute() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return "done"

    results = await asyncio.gather(*(cache.get_or_compute("k", compute) for _ in range(6)))

    assert results == ["done"] * 6 and calls == 1
    assert not cache._locks  # nothing left behind


async def test_failed_compute_is_not_cached() -> None:
    cache = ResultCache()

    async def boom() -> str:
        raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        await cache.get_or_compute("k", boom)
    assert await cache.get_or_compute("k", lambda: _value("ok")) == "ok"


async def _value(value: str) -> str:
    return value


# ------------------------------------------------------------------ sector cache
class _CountingRepository(MemoryQuantRepository):
    def __init__(self) -> None:
        super().__init__()
        self.loads = 0

    async def list_market_sector_days(self, dates, *, as_of=None):  # type: ignore[override]
        self.loads += 1
        return await super().list_market_sector_days(dates, as_of=as_of)


async def _seed(repo: MemoryQuantRepository, stocks: int = 60) -> None:
    await repo.upsert_market_stock_days(build_rows(stocks, 32))
    from market_perf_fixture import build_industries

    await repo.upsert_market_industries(build_industries(stocks))


async def test_cache_hit_skips_the_load_and_a_sync_invalidates_it() -> None:
    repo = _CountingRepository()
    await _seed(repo)
    service = SectorRotationService(repo, clock=lambda: MONDAY_AFTER_CLOSE)

    first = await service.rotation(window=5)
    again = await service.rotation(window=5)
    other_window = await service.rotation(window=20)
    stocks = await service.stocks("24", window=5)
    assert repo.loads == 1  # one load serves every window and the drill-down
    assert again == first and other_window.window == 20 and stocks.entries

    # A sync writes the newest session again with a changed price: new data version.
    newest = max(repo.market_stock_days.values(), key=lambda r: r.trading_date)
    await repo.upsert_market_stock_days(
        [newest.model_copy(update={"close": newest.close * 1.5, "change": newest.change + 1})]
    )
    refreshed = await service.rotation(window=5)
    assert repo.loads == 2 and refreshed != first

    # Another point in time is another entry.
    await service.rotation(window=5, as_of=date(2026, 10, 20))
    assert repo.loads == 3
    await service.rotation(window=5, as_of=date(2026, 10, 20))
    assert repo.loads == 3


async def test_pending_backfill_flag_is_never_served_stale() -> None:
    repo = MemoryQuantRepository()
    await _seed(repo)
    service = SectorRotationService(repo, clock=lambda: MONDAY_AFTER_CLOSE)
    assert (await service.rotation(window=5)).backfill_pending is False

    from quant_signal.domain.models import SyncJobKind

    await repo.enqueue_sync_job(SyncJobKind.MARKET, "TW", lookback_days=30)
    assert (await service.rotation(window=5)).backfill_pending is True


async def test_intraday_results_are_cached_per_ten_minute_slot() -> None:
    repo = _CountingRepository()
    await _seed(repo, 60)
    from market_perf_fixture import build_snapshots

    await repo.upsert_intraday_snapshots(build_snapshots(60, 0.9))
    now = [MONDAY_IN_SESSION]
    service = SectorRotationService(repo, clock=lambda: now[0])

    live = await service.rotation(window=5)
    assert live.mode == "intraday"
    now[0] += timedelta(minutes=3)
    await service.rotation(window=5)
    assert repo.loads == 1  # same slot
    now[0] += timedelta(minutes=10)
    await service.rotation(window=5)
    assert repo.loads == 2  # next slot reloads


async def test_stored_today_needs_no_slot() -> None:
    """Once the day's own row is stored the intraday stand-in no longer applies."""
    repo = _CountingRepository()
    await repo.upsert_market_stock_days(build_rows(40, 32, last=date(2026, 11, 2)))
    now = [MONDAY_AFTER_CLOSE]
    service = SectorRotationService(repo, clock=lambda: now[0])
    await service.rotation(window=5)
    now[0] += timedelta(minutes=30)
    await service.rotation(window=5)
    assert repo.loads == 1


# --------------------------------------------------------------------- event loop
async def test_sector_work_runs_off_the_event_loop(monkeypatch) -> None:
    repo = MemoryQuantRepository()
    await _seed(repo)
    seen: dict[str, str] = {}
    for name in ("_group_sessions", "_assemble"):
        original = getattr(sectors_module, name)

        def spy(*args, _original=original, _name=name):
            seen[_name] = threading.current_thread().name
            return _original(*args)

        monkeypatch.setattr(sectors_module, name, spy)
    original_of = SectorRotationService._rotation_of

    def spy_of(self, *args):
        seen["_rotation_of"] = threading.current_thread().name
        return original_of(self, *args)

    monkeypatch.setattr(SectorRotationService, "_rotation_of", spy_of)

    await SectorRotationService(repo, clock=lambda: MONDAY_AFTER_CLOSE).rotation(window=5)

    main = threading.main_thread().name
    assert set(seen) == {"_group_sessions", "_assemble", "_rotation_of"}
    assert all(thread != main for thread in seen.values()), seen


async def test_heavy_sector_request_does_not_delay_other_requests() -> None:
    """A whole-market sector computation must not hold up a trivial request."""
    repo = MemoryQuantRepository()
    await repo.upsert_market_stock_days(build_rows(1800, 32))
    await repo.upsert_market_industries(__import__("market_perf_fixture").build_industries(1800))

    async def override():
        yield repo

    app.dependency_overrides[get_repository] = override
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/health")  # warm the stack
            heavy = asyncio.create_task(client.get("/api/v1/market/sectors", params={"window": 5}))
            await asyncio.sleep(0)
            latencies: list[float] = []
            gaps: list[float] = []
            last = clock.perf_counter()
            while not heavy.done():
                now = clock.perf_counter()
                gaps.append(now - last)
                last = now
                started = clock.perf_counter()
                light = await client.get("/health")
                latencies.append(clock.perf_counter() - started)
                assert light.status_code == 200
                await asyncio.sleep(0.002)
            response = await heavy
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200 and response.json()["sectors"]
    assert len(latencies) >= 5, "the sector request finished too fast to prove anything"
    gaps.append(clock.perf_counter() - last)
    # Neither a trivial request nor the loop itself was held up for long.
    assert max(latencies) < 0.05, f"a request took {max(latencies) * 1000:.0f} ms"
    assert max(gaps) < 0.05, f"the loop stalled for {max(gaps) * 1000:.0f} ms"


# ------------------------------------------------------------- repository helpers
async def test_market_ranking_rows_carry_the_newest_session_fields() -> None:
    repo = MemoryQuantRepository()
    await repo.upsert_market_stock_days(build_rows(20, 6))
    dates = sorted({r.trading_date for r in repo.market_stock_days.values()})[-3:]
    rows = await repo.market_ranking("volume", dates, None, as_of=None, order="desc", limit=3)
    assert len(rows) == 3 and all(isinstance(r, MarketRankingRow) for r in rows)
    assert [r.value for r in rows] == sorted((r.value for r in rows), reverse=True)
    newest = {r.symbol: r for r in repo.market_stock_days.values() if r.trading_date == dates[-1]}
    assert all(row.close == newest[row.symbol].close for row in rows)


def test_ranking_raw_value_handles_unknowns() -> None:
    class R:
        def __init__(self, **kw: Any) -> None:
            self.__dict__.update(kw)

    assert ranking_raw_value("total_net", [R(total_net=None), R(total_net=None)]) is None
    assert ranking_raw_value("total_net", [R(total_net=5), R(total_net=None)]) == 5
    assert (
        ranking_raw_value("change_pct", [R(close=10.0, change=10.0), R(close=5.0, change=0)])
        is None
    )
    with pytest.raises(ValueError):
        ranking_raw_value("nope", [])


# ---------------------------------------------------- status and flows are cached too
async def test_status_and_flows_follow_the_data_version() -> None:
    repo = MemoryQuantRepository()
    rows = build_rows(30, 8)
    await repo.upsert_market_stock_days(rows)
    service = MarketRankingService(repo)

    status = await service.status()
    flows = await service.flows(days=5)
    assert await service.status() == status and (await service.flows(days=5)) == flows

    # The sync stores a new session; the cached answers must not survive it.
    newest = max(row.trading_date for row in rows)
    day = newest + timedelta(days=3)
    await repo.upsert_market_stock_days(
        [row.model_copy(update={"trading_date": day}) for row in rows if row.trading_date == newest]
    )
    assert (await service.status())[0].last_date == day != status[0].last_date
    assert (await service.flows(days=5)).data_through == day
    # The cached copy handed out earlier was not mutated by the refresh.
    assert flows.data_through == newest
