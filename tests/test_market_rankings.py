from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.application.market_rankings import MarketRankingService
from quant_signal.application.sync_jobs import ensure_market_snapshots
from quant_signal.domain.models import JobStatus, MarketStockDay, SyncJobKind
from quant_signal.infrastructure.memory import MemoryQuantRepository

D1, D2, D3 = date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30)


def _row(
    symbol: str,
    day: date,
    *,
    close: float = 100.0,
    change: float = 0.0,
    foreign: int | None = 0,
    margin_change: int | None = 0,
    volume: int = 1_000_000,
    market: str = "TW",
    name: str | None = None,
) -> MarketStockDay:
    return MarketStockDay(
        symbol=symbol,
        name=name or symbol,
        market=market,  # type: ignore[arg-type]
        trading_date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        turnover_value=close * volume,
        change=change,
        foreign_net=foreign,
        trust_net=foreign if foreign is None else 0,
        dealer_net=foreign if foreign is None else 0,
        total_net=foreign,
        margin_change=margin_change,
        margin_balance=1000,
        short_change=0,
        short_balance=10,
        available_at=datetime.combine(day, time(10), tzinfo=UTC),
        source="twse_official" if market == "TW" else "tpex_official",
    )


@pytest.fixture
async def repository() -> MemoryQuantRepository:
    repo = MemoryQuantRepository()
    await repo.upsert_market_stock_days(
        [
            # AAA: strong foreign buying, rising
            _row("1101.TW", D1, close=100, change=1, foreign=2_000_000),
            _row("1101.TW", D2, close=102, change=2, foreign=3_000_000),
            _row("1101.TW", D3, close=105, change=3, foreign=-1_000_000),
            # BBB: heavy selling, falling
            _row("1102.TW", D1, close=50, change=-1, foreign=-4_000_000),
            _row("1102.TW", D2, close=48, change=-2, foreign=-5_000_000),
            _row("1102.TW", D3, close=45, change=-3, foreign=-1_000_000),
            # OTC name
            _row("6488.TWO", D1, close=700, change=5, foreign=500_000, market="TWO"),
            _row("6488.TWO", D2, close=710, change=10, foreign=500_000, market="TWO"),
            _row("6488.TWO", D3, close=720, change=10, foreign=500_000, market="TWO"),
            # Stopped trading before the latest day: must never be ranked
            _row("1103.TW", D1, close=10, foreign=9_000_000),
            _row("1103.TW", D2, close=10, foreign=9_000_000),
        ]
    )
    return repo


async def test_one_day_foreign_net_ranks_buyers_then_sellers(repository) -> None:
    service = MarketRankingService(repository)

    buyers = await service.ranking("foreign_net", window=1, order="desc")
    sellers = await service.ranking("foreign_net", window=1, order="asc")

    assert buyers.data_through == D3 and buyers.trading_days == 1
    # D3 (lots): 6488 +500, 1101 -1,000, 1102 -1,000
    assert [e.symbol for e in buyers.entries][0] == "6488.TWO"
    assert buyers.entries[0].value == 500
    assert {e.symbol for e in sellers.entries[:2]} == {"1101.TW", "1102.TW"}
    assert buyers.unit == "張"
    assert [e.rank for e in sellers.entries] == [1, 2, 3]


async def test_window_sums_and_converts_shares_to_lots(repository) -> None:
    ranking = await MarketRankingService(repository).ranking(
        "foreign_net", window=3, order="desc"
    )

    by_symbol = {e.symbol: e.value for e in ranking.entries}
    assert by_symbol["1101.TW"] == 4_000  # (2 + 3 - 1) million shares / 1000
    assert by_symbol["1102.TW"] == -10_000
    assert by_symbol["6488.TWO"] == 1_500
    assert ranking.entries[0].symbol == "1101.TW"
    assert ranking.trading_days == 3 and ranking.window_start == D1


async def test_delisted_or_halted_names_are_not_ranked(repository) -> None:
    ranking = await MarketRankingService(repository).ranking(
        "foreign_net", window=3, order="desc", limit=50
    )

    assert "1103.TW" not in {e.symbol for e in ranking.entries}


async def test_market_filter(repository) -> None:
    otc = await MarketRankingService(repository).ranking(
        "turnover", market="TWO", window=1
    )

    assert [e.symbol for e in otc.entries] == ["6488.TWO"]
    assert otc.entries[0].market == "TWO"


async def test_change_pct_compounds_over_the_window(repository) -> None:
    ranking = await MarketRankingService(repository).ranking(
        "change_pct", window=3, order="desc"
    )

    top = ranking.entries[0]
    # 1101: oldest day's reference = 100 - 1 = 99; latest close 105
    assert top.symbol in {"1101.TW", "6488.TWO"}
    value = {e.symbol: e.value for e in ranking.entries}
    assert value["1101.TW"] == pytest.approx((105 / 99 - 1) * 100)
    assert value["1102.TW"] == pytest.approx((45 / 51 - 1) * 100)
    assert ranking.unit == "%"


async def test_day_change_pct_uses_the_previous_close(repository) -> None:
    ranking = await MarketRankingService(repository).ranking("volume", window=1)

    entry = next(e for e in ranking.entries if e.symbol == "1101.TW")
    assert entry.day_change_pct == pytest.approx(3 / 102 * 100)
    assert entry.volume_lots == 1000


async def test_as_of_only_uses_data_published_by_then(repository) -> None:
    ranking = await MarketRankingService(repository).ranking(
        "foreign_net", window=1, as_of=D2
    )

    assert ranking.data_through == D2
    assert {e.symbol: e.value for e in ranking.entries}["1101.TW"] == 3_000


async def test_flow_series_values_are_net_shares_times_close(repository) -> None:
    series = await MarketRankingService(repository).flows(days=3)

    first = series.days[0]
    assert first.trading_date == D1
    # 1101: 2,000,000 x 100 ; 1102: -4,000,000 x 50 ; 6488: 500,000 x 700 ; 1103: 9,000,000 x 10
    assert first.foreign_net_value == 200_000_000 - 200_000_000 + 350_000_000 + 90_000_000
    assert first.advancing == 2 and first.declining == 1 and first.unchanged == 1
    assert series.data_through == D3
    assert "estimate" in series.value_basis


async def test_empty_table_starts_a_backfill_once() -> None:
    repository = MemoryQuantRepository()
    service = MarketRankingService(repository, backfill_days=200)

    first = await service.ranking("foreign_net")
    second = await service.ranking("foreign_net")

    assert first.entries == [] and first.backfill_pending and second.backfill_pending
    jobs = [j for j in repository.sync_jobs.values() if j.kind is SyncJobKind.MARKET]
    assert sorted(j.symbol for j in jobs) == ["TW", "TWO"]
    assert all(j.lookback_days == 200 and j.status is JobStatus.QUEUED for j in jobs)


async def test_bootstrap_does_nothing_when_data_exists(repository) -> None:
    assert await ensure_market_snapshots(repository, lookback_days=100) == 0
    assert repository.sync_jobs == {}


async def test_status_reports_coverage(repository) -> None:
    status = {s.market: s for s in await MarketRankingService(repository).status()}

    assert status["TW"].trading_days == 3
    assert status["TW"].last_date == D3
    assert status["TWO"].institutional_coverage == 1


async def test_rest_endpoints(repository) -> None:
    async def override():
        yield repository

    app.dependency_overrides[get_repository] = override
    try:
        with TestClient(app) as client:
            ranking = client.get(
                "/api/v1/market/rankings",
                params={"metric": "foreign_net", "window": 5, "limit": 2},
            )
            flows = client.get("/api/v1/market/flows", params={"days": 5})
            status = client.get("/api/v1/market/data-status")
            bad = client.get("/api/v1/market/rankings", params={"metric": "bogus"})
            bad_window = client.get("/api/v1/market/rankings", params={"window": 99})
    finally:
        app.dependency_overrides.clear()

    assert ranking.status_code == 200
    body = ranking.json()
    assert body["entries"][0]["symbol"] == "1101.TW" and len(body["entries"]) == 2
    assert flows.status_code == 200 and len(flows.json()["days"]) == 3
    assert status.status_code == 200 and {s["market"] for s in status.json()} == {"TW", "TWO"}
    assert bad.status_code == 422 and bad_window.status_code == 422


def test_fixture_days_are_consecutive() -> None:
    assert D2 - D1 == timedelta(days=1) == D3 - D2


async def test_flow_rankings_fall_back_to_the_latest_published_report() -> None:
    repo = MemoryQuantRepository()
    d4 = date(2026, 10, 31)
    await repo.upsert_market_stock_days(
        [
            _row("1101.TW", D3, foreign=2_000_000),
            # Quotes are out for D4 but the institutional report is not yet.
            _row("1101.TW", d4, foreign=None, margin_change=None),
        ]
    )
    service = MarketRankingService(repo)

    flows = await service.ranking("foreign_net", window=1)
    volume = await service.ranking("volume", window=1)
    series = await service.flows(days=5)

    assert flows.data_through == D3 and flows.awaiting_report_date == d4
    assert flows.entries[0].value == 2000
    assert volume.data_through == d4 and volume.awaiting_report_date is None
    assert [d.institutional_reported for d in series.days] == [True, False]


async def test_all_markets_must_report_before_a_session_counts() -> None:
    repo = MemoryQuantRepository()
    d4 = date(2026, 10, 31)
    await repo.upsert_market_stock_days(
        [
            _row("1101.TW", D3, foreign=1_000_000),
            _row("6488.TWO", D3, foreign=1_000_000, market="TWO"),
            # d4: OTC has published, listed has not.
            _row("1101.TW", d4, foreign=None),
            _row("6488.TWO", d4, foreign=7_000_000, market="TWO"),
        ]
    )
    service = MarketRankingService(repo)

    everything = await service.ranking("foreign_net", market="ALL")
    otc_only = await service.ranking("foreign_net", market="TWO")
    listed = await service.ranking("foreign_net", market="TW")
    series = await service.flows(market="ALL", days=5)

    assert everything.data_through == D3 and everything.awaiting_report_date == d4
    assert otc_only.data_through == d4 and otc_only.awaiting_report_date is None
    assert listed.data_through == D3 and listed.awaiting_report_date == d4
    assert [d.institutional_reported for d in series.days] == [True, False]
