from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.adapters.rest.security import Principal, require_principal
from quant_signal.application.services import InstrumentSearchService
from quant_signal.application.stocks import (
    StockNotFound,
    StockOverviewService,
    WatchlistFull,
    WatchlistService,
)
from quant_signal.domain.models import Instrument, MarketStockDay, SyncJobKind, User
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.settings import get_settings

DAYS = [date(2026, 10, 26) + timedelta(days=i) for i in range(7)]  # 7 sessions
ME = uuid4()


def _row(symbol: str, day: date, *, close: float, name: str, market: str = "TW", **kw):
    fields = {
        "open": close,
        "high": close,
        "low": close,
        "volume": 3_000_000,
        "turnover_value": close * 3_000_000,
        "change": 1.0,
        "foreign_net": 1_500_000,
        "trust_net": 0,
        "dealer_net": 500_000,
        "total_net": 2_000_000,
        "margin_balance": 800,
        "margin_change": 5,
        "short_balance": 20,
        "short_change": -1,
    }
    fields.update(kw)
    return MarketStockDay(
        symbol=symbol,
        name=name,
        market=market,  # type: ignore[arg-type]
        trading_date=day,
        close=close,
        available_at=datetime.combine(day, time(10), tzinfo=UTC),
        source="twse_official" if market == "TW" else "tpex_official",
        **fields,
    )


@pytest.fixture
async def repo() -> MemoryQuantRepository:
    repository = MemoryQuantRepository()
    rows = []
    for index, day in enumerate(DAYS):
        rows.append(_row("2330.TW", day, close=1000 + index * 10, name="台積電"))
        rows.append(_row("2317.TW", day, close=200 + index, name="鴻海"))
        rows.append(_row("6488.TWO", day, close=700, name="環球晶", market="TWO", foreign_net=None))
    await repository.upsert_market_stock_days(rows)
    return repository


async def test_search_covers_untracked_stocks_by_code_and_name(repo) -> None:
    service = InstrumentSearchService(repo)

    by_code = await service.search("2330")
    by_name = await service.search("環球")

    assert [(i.symbol, i.tracked) for i in by_code] == [("2330.TW", False)]
    assert [i.symbol for i in by_name] == ["6488.TWO"]
    assert await service.search("no-such-stock") == []


async def test_search_prefers_the_tracked_entry_and_flags_it(repo) -> None:
    repo.instruments["2330.TW"] = Instrument(symbol="2330.TW", name="台積電", market="TW")

    results = await InstrumentSearchService(repo).search("台積")

    assert [(i.symbol, i.tracked) for i in results] == [("2330.TW", True)]  # no duplicate


async def test_search_ranks_exact_code_before_partial_matches(repo) -> None:
    await repo.upsert_market_stock_days(
        [_row("23301.TW", d, close=10, name="假設公司") for d in DAYS]
    )

    results = await InstrumentSearchService(repo).search("2330")

    assert [i.symbol for i in results] == ["2330.TW", "23301.TW"]


async def test_overview_is_served_from_the_snapshot_and_queues_full_history(repo) -> None:
    overview = await StockOverviewService(repo).get("2330")  # bare code is accepted

    assert overview.symbol == "2330.TW" and overview.name == "台積電"
    assert overview.data_through == DAYS[-1] and overview.trading_days == 7
    assert overview.full_analysis == "preparing"
    last = overview.days[-1]
    assert last.close == 1060 and last.volume_lots == 3000
    assert last.foreign_net_lots == 1500 and last.total_net_lots == 2000
    assert last.change_pct == pytest.approx(0.09, abs=0.005)  # +1 on a 1059 base
    assert overview.summary.return_5d_pct == pytest.approx(5.0, abs=0.1)
    assert overview.summary.foreign_net_5d_lots == 7500
    # The background job is queued once and dedupes on repeat views.
    await StockOverviewService(repo).get("2330.TW")
    jobs = [j for j in repo.sync_jobs.values() if j.symbol == "2330.TW"]
    assert sorted(j.kind for j in jobs) == sorted([SyncJobKind.SYMBOL, SyncJobKind.INSTITUTIONS])


async def test_overview_keeps_unpublished_flows_unknown(repo) -> None:
    overview = await StockOverviewService(repo).get("6488.TWO")

    assert overview.days[-1].foreign_net_lots is None
    assert overview.summary.foreign_net_5d_lots is None


async def test_overview_rejects_unlisted_symbols(repo) -> None:
    with pytest.raises(StockNotFound):
        await StockOverviewService(repo).get("9999")


async def test_watchlist_add_list_remove_and_background_sync(repo) -> None:
    service = WatchlistService(repo, limit=25)

    assert await service.add(ME, "2330") == "added"
    assert await service.add(ME, "2330.TW") == "exists"  # idempotent
    await service.add(ME, "6488.TWO")

    listing = await service.get(ME)
    assert (listing.limit, listing.count) == (25, 2)
    first = listing.items[0]
    assert first.symbol == "2330.TW" and first.close == 1060 and first.full_analysis == "preparing"
    assert first.total_net_5d_lots == 10000
    assert any(j.symbol == "2330.TW" for j in repo.sync_jobs.values())

    assert await service.remove(ME, "2330.tw") is True
    assert await service.remove(ME, "2330.TW") is False
    await service.add(ME, "2317.TW")
    assert await service.remove(ME, "2317") is True  # bare code resolves in the user's list
    assert await service.remove(ME, "9999") is False
    assert [i.symbol for i in (await service.get(ME)).items] == ["6488.TWO"]


async def test_watchlist_rejects_unknown_symbols(repo) -> None:
    with pytest.raises(StockNotFound):
        await WatchlistService(repo, limit=25).add(ME, "0000.TW")


async def test_watchlist_is_capped_per_user_and_removal_frees_a_slot(repo) -> None:
    service = WatchlistService(repo, limit=2)
    await service.add(ME, "2330.TW")
    await service.add(ME, "2317.TW")

    with pytest.raises(WatchlistFull) as error:
        await service.add(ME, "6488.TWO")
    assert "上限 2 檔" in str(error.value)
    assert await service.add(ME, "2317.TW") == "exists"  # re-adding is not "full"
    # Another user has their own list.
    assert await service.add(uuid4(), "6488.TWO") == "added"

    await service.remove(ME, "2317.TW")
    assert await service.add(ME, "6488.TWO") == "added"


# --- HTTP -----------------------------------------------------------------


@pytest.fixture
def client(repo):
    user = User(
        id=ME, google_sub="g-1", email="me@gmail.com", created_at=datetime.now(UTC)
    )

    async def override():
        yield repo

    app.dependency_overrides[get_repository] = override
    app.dependency_overrides[require_principal] = lambda: Principal("user", user=user)
    settings = get_settings().model_copy(update={"watchlist_max_items": 2})
    app.dependency_overrides[get_settings] = lambda: settings
    from quant_signal.adapters.rest import main as rest_main

    original = rest_main.get_settings
    rest_main.get_settings = lambda: settings
    with TestClient(app) as c:
        yield c
    rest_main.get_settings = original
    app.dependency_overrides.clear()


def test_http_watchlist_flow_and_limit(client) -> None:
    assert client.get("/api/v1/watchlist").json()["items"] == []

    added = client.put("/api/v1/watchlist/2330.TW")
    assert added.status_code == 200 and added.json()["count"] == 1
    assert client.put("/api/v1/watchlist/2317.TW").status_code == 200
    full = client.put("/api/v1/watchlist/6488.TWO")
    assert full.status_code == 409 and "上限 2 檔" in full.json()["detail"]
    assert client.put("/api/v1/watchlist/0000.TW").status_code == 404

    removed = client.delete("/api/v1/watchlist/2317.TW").json()
    assert [i["symbol"] for i in removed["items"]] == ["2330.TW"]


def test_http_overview_and_search_include_untracked_stocks(client) -> None:
    overview = client.get("/api/v1/stocks/2330/overview")
    assert overview.status_code == 200 and overview.json()["full_analysis"] == "preparing"
    assert client.get("/api/v1/stocks/0000/overview").status_code == 404
    found = client.get("/api/v1/instruments/search", params={"q": "鴻海"}).json()
    assert found == [{"symbol": "2317.TW", "name": "鴻海", "market": "TW", "tracked": False}]


def test_watchlist_needs_a_signed_in_user(repo) -> None:
    async def override():
        yield repo

    app.dependency_overrides[get_repository] = override
    app.dependency_overrides[require_principal] = lambda: Principal("anonymous")
    try:
        with TestClient(app) as anonymous:
            assert anonymous.get("/api/v1/watchlist").status_code == 403
            assert anonymous.put("/api/v1/watchlist/2330.TW").status_code == 403
    finally:
        app.dependency_overrides.clear()


async def test_flow_windows_skip_a_session_whose_report_is_not_out(repo) -> None:
    unpublished = [
        _row("2330.TW", DAYS[-1] + timedelta(days=1), close=1070, name="台積電",
             foreign_net=None, trust_net=None, dealer_net=None, total_net=None)
    ]
    await repo.upsert_market_stock_days(unpublished)

    overview = await StockOverviewService(repo).get("2330")

    assert overview.data_through == DAYS[-1] + timedelta(days=1)
    assert overview.summary.flows_through == DAYS[-1]
    assert overview.summary.foreign_net_5d_lots == 7500  # five published sessions, not four
    service = WatchlistService(repo, limit=25)
    await service.add(ME, "2330.TW")
    item = (await service.get(ME)).items[0]
    assert item.foreign_net_1d_lots == 1500 and item.flows_through == DAYS[-1]


def test_cors_preflight_allows_watchlist_mutations(client) -> None:
    for method in ("PUT", "DELETE"):
        response = client.options(
            "/api/v1/watchlist/2330.TW",
            headers={
                "Origin": "http://localhost:13000",
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 200, method
        assert method in response.headers["access-control-allow-methods"]
