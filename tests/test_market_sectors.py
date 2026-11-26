from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.application.intraday_snapshots import (
    floor_to_slot,
    prune_intraday_snapshots,
    store_intraday_snapshots,
)
from quant_signal.application.market_industry import refresh_industries
from quant_signal.application.market_sectors import SectorRotationService, rrg_points
from quant_signal.domain.models import (
    IntradaySnapshot,
    MarketIndustry,
    MarketStockDay,
)
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.infrastructure.providers.industry import (
    parse_tpex_industries,
    parse_twse_industries,
)

FRIDAY = date(2026, 10, 30)
MONDAY = date(2026, 11, 2)
# Monday 10:35 Taipei, inside the session.
IN_SESSION = datetime(2026, 11, 2, 2, 35, tzinfo=UTC)
AFTER_CLOSE = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)  # 17:00 Taipei
SATURDAY = datetime(2026, 10, 31, 3, 0, tzinfo=UTC)


def _sessions(count: int = 25) -> list[date]:
    days: list[date] = []
    cursor = FRIDAY
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


def _stock_days(
    symbol: str,
    sessions: list[date],
    daily: float | list[float],
    *,
    start: float = 100.0,
    turnover: float = 1_000_000_000.0,
    net: int | None = 0,
) -> list[MarketStockDay]:
    rows: list[MarketStockDay] = []
    previous = start
    for index, day in enumerate(sessions):
        close = previous * (1 + (daily[index] if isinstance(daily, list) else daily))
        rows.append(
            MarketStockDay(
                symbol=symbol,
                name=f"N{symbol[:4]}",
                market="TWO" if symbol.endswith(".TWO") else "TW",
                trading_date=day,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1_000_000,
                turnover_value=turnover,
                change=close - previous,
                foreign_net=net,
                trust_net=net,
                dealer_net=net,
                total_net=net,
                available_at=datetime.combine(day, time(10), tzinfo=UTC),
                source="twse_official",
            )
        )
        previous = close
    return rows


def _industry(symbol: str, code: str, name: str) -> MarketIndustry:
    return MarketIndustry(
        symbol=symbol,
        market="TWO" if symbol.endswith(".TWO") else "TW",
        industry_code=code,
        industry_name=name,
        source="twse_openapi",
        available_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


@pytest.fixture
async def repository() -> MemoryQuantRepository:
    repo = MemoryQuantRepository()
    sessions = _sessions()
    rows: list[MarketStockDay] = []
    # Semis: rising 1% a day, with institutional buying. Construction: flat.
    rows += _stock_days("2330.TW", sessions, 0.01, turnover=3e9, net=2_000_000)
    rows += _stock_days("2454.TW", sessions, 0.01, turnover=1e9, net=1_000_000)
    rows += _stock_days("2501.TW", sessions, 0.0, turnover=1e9, net=-500_000)
    rows += _stock_days("2504.TWO", sessions, 0.0, turnover=1e9, net=-500_000)
    # Not in any industry table: counted as unclassified.
    rows += _stock_days("9999.TW", sessions, 0.0, turnover=1e8)
    await repo.upsert_market_stock_days(rows)
    await repo.upsert_market_industries(
        [
            _industry("2330.TW", "24", "半導體業"),
            _industry("2454.TW", "24", "半導體業"),
            _industry("2501.TW", "14", "建材營造"),
            _industry("2504.TWO", "14", "建材營造"),
        ]
    )
    return repo


def _service(repo: MemoryQuantRepository, now: datetime = AFTER_CLOSE) -> SectorRotationService:
    return SectorRotationService(repo, clock=lambda: now)


async def test_returns_turnover_breadth_and_institutional_per_industry(repository) -> None:
    result = await _service(repository).rotation(window=5)

    assert result.mode == "close" and result.data_through == FRIDAY
    assert result.unclassified_count == 1
    by_code = {row.code: row for row in result.sectors}
    semis, build = by_code["24"], by_code["14"]
    expected = (1.01**5 - 1) * 100
    assert semis.returns["5"].equal_pct == pytest.approx(expected)
    assert semis.returns["1"].equal_pct == pytest.approx(1.0)
    assert semis.returns["20"].equal_pct == pytest.approx((1.01**20 - 1) * 100)
    assert build.returns["5"].equal_pct == pytest.approx(0.0)
    # Turnover-weighted: both semis rose equally, so the weighting changes nothing.
    assert semis.returns["5"].weighted_pct == pytest.approx(expected)
    # Shares of the whole market's turnover (incl. the unclassified name).
    market = 3e9 + 1e9 + 1e9 + 1e9 + 1e8
    assert semis.turnover_share == pytest.approx(4e9 / market)
    assert semis.turnover_share_avg20 == pytest.approx(4e9 / market)
    assert semis.turnover_share_change == pytest.approx(0.0, abs=1e-12)
    assert semis.breadth_up_pct == 100 and build.breadth_up_pct == 0
    assert semis.up_count == 2 and build.flat_count == 2
    # 5 sessions x (2,000,000 + 1,000,000) shares = 15,000 lots
    assert semis.institutional_net_lots == pytest.approx(15_000)
    assert build.institutional_net_lots == pytest.approx(-5_000)
    assert result.institutional_through == FRIDAY
    assert result.sectors[0].code == "24"  # sorted by turnover share


async def test_turnover_share_change_flags_money_moving_in(repository) -> None:
    last = _sessions()[-1]
    # Construction turnover triples on the last session only.
    await repository.upsert_market_stock_days(
        [
            row.model_copy(update={"turnover_value": row.turnover_value * 3})
            for row in _stock_days("2501.TW", [last], 0.0, turnover=1e9)
        ]
    )
    result = await _service(repository).rotation(window=1)

    build = next(row for row in result.sectors if row.code == "14")
    assert build.turnover_share_change is not None and build.turnover_share_change > 0.03
    semis = next(row for row in result.sectors if row.code == "24")
    assert semis.turnover_share_change is not None and semis.turnover_share_change < 0


async def test_rrg_quadrants_follow_relative_strength_and_momentum() -> None:
    repo = MemoryQuantRepository()
    sessions = _sessions()
    count = len(sessions)
    await repo.upsert_market_stock_days(
        [
            *_stock_days("2330.TW", sessions, [0.0008 * i for i in range(count)]),
            *_stock_days("2501.TW", sessions, [-0.0008 * i for i in range(count)]),
            *_stock_days("1101.TW", sessions, 0.0),
        ]
    )
    await repo.upsert_market_industries(
        [
            _industry("2330.TW", "24", "半導體業"),
            _industry("2501.TW", "14", "建材營造"),
            _industry("1101.TW", "01", "水泥工業"),
        ]
    )

    result = await _service(repo).rotation()

    by_code = {row.code: row for row in result.sectors}
    leader, laggard = by_code["24"].rrg, by_code["14"].rrg
    assert leader is not None and laggard is not None
    assert leader.quadrant == "leading"
    assert leader.rs_ratio > 100 and leader.rs_momentum > 100
    assert laggard.quadrant == "lagging"
    assert laggard.rs_ratio < 100 and laggard.rs_momentum < 100
    assert len(leader.trail) == 5


def test_rrg_detects_a_sector_that_peaked_and_rolled_over() -> None:
    market = [0.0] * 30
    sector = [0.01] * 20 + [-0.01] * 10
    points = rrg_points(sector, market)

    assert points[5].rs_ratio > 100 and points[5].rs_momentum > 100  # leading
    assert points[-1].rs_momentum < 100  # momentum has turned down
    assert rrg_points([0.01] * 8, [0.0] * 8) == []


async def test_rotation_never_uses_data_published_after_the_as_of_date(repository) -> None:
    sessions = _sessions()
    early = sessions[-3]
    result = await _service(repository).rotation(window=1, as_of=early)

    assert result.data_through == early
    assert result.intraday_status == "not_applicable"
    assert result.mode == "close"


async def test_rows_not_yet_available_are_ignored_at_query_time(repository) -> None:
    # Clock set before the last session's rows became available (10:00 UTC that day).
    before = datetime.combine(FRIDAY, time(9), tzinfo=UTC)
    result = await _service(repository, before).rotation(window=1)

    assert result.data_through == _sessions()[-2]


async def test_intraday_snapshot_replaces_missing_today_row(repository) -> None:
    taken = datetime(2026, 11, 2, 2, 30, tzinfo=UTC)  # 10:30 Taipei
    prices = {"2330.TW": 1.03, "2454.TW": 1.01, "2501.TW": 0.98, "2504.TWO": 0.99}
    closes = {
        symbol: next(
            row.close
            for row in repository.market_stock_days.values()
            if row.symbol == symbol and row.trading_date == FRIDAY
        )
        for symbol in prices
    }
    await store_intraday_snapshots(
        [
            IntradaySnapshot(
                symbol=symbol,
                taken_at=taken,
                price=closes[symbol] * factor,
                change_pct=factor - 1,
                volume_lots=500,
                amount_twd=5e8,
                available_at=taken + timedelta(seconds=20),
            )
            for symbol, factor in prices.items()
        ]
        + [  # a later slot that is not yet available at 10:35
            IntradaySnapshot(
                symbol="2330.TW",
                taken_at=taken + timedelta(minutes=10),
                price=closes["2330.TW"] * 1.09,
                change_pct=0.09,
                volume_lots=900,
                amount_twd=9e8,
                available_at=taken + timedelta(minutes=10, seconds=20),
            )
        ],
        repository,
    )

    result = await _service(repository, IN_SESSION).rotation(window=1)

    assert result.mode == "intraday" and result.intraday_status == "live"
    assert result.data_through == MONDAY
    assert result.data_time == taken
    by_code = {row.code: row for row in result.sectors}
    assert by_code["24"].returns["1"].equal_pct == pytest.approx(2.0)  # (3% + 1%) / 2
    assert by_code["14"].returns["1"].equal_pct == pytest.approx(-1.5)
    assert by_code["14"].breadth_up_pct == 0
    # Today's institutional report does not exist yet: 1-session flow is unknown.
    assert by_code["24"].institutional_net_lots is None

    listing = await _service(repository, IN_SESSION).stocks("24", window=1)
    assert [entry.symbol for entry in listing.entries] == ["2330.TW", "2454.TW"]
    assert listing.mode == "intraday"


async def test_without_snapshots_session_hours_report_unavailable(repository) -> None:
    during = await _service(repository, IN_SESSION).rotation(window=1)
    after = await _service(repository, AFTER_CLOSE).rotation(window=1)
    weekend = await _service(repository, SATURDAY).rotation(window=1)

    assert during.mode == "close" and during.intraday_status == "unavailable"
    assert during.data_through == FRIDAY
    assert after.intraday_status == "not_applicable"
    assert weekend.intraday_status == "not_applicable"


async def test_thin_snapshot_coverage_falls_back_to_close(repository) -> None:
    taken = datetime(2026, 11, 2, 2, 30, tzinfo=UTC)
    await store_intraday_snapshots(
        [
            IntradaySnapshot(
                symbol="2330.TW",
                taken_at=taken,
                price=100,
                change_pct=0.0,
                volume_lots=1,
                amount_twd=1.0,
                available_at=taken,
            )
        ],
        repository,
    )

    result = await _service(repository, IN_SESSION).rotation(window=1)

    assert result.mode == "close" and result.intraday_status == "unavailable"


async def test_sector_stocks_sort_and_empty_code(repository) -> None:
    service = _service(repository)
    asc = await service.stocks("14", window=5, sort="institutional", order="asc")
    none = await service.stocks("99")

    assert asc.name == "建材營造"
    assert {entry.symbol for entry in asc.entries} == {"2501.TW", "2504.TWO"}
    assert all(entry.institutional_net_lots == pytest.approx(-2_500) for entry in asc.entries)
    assert none.entries == []


async def test_empty_table_returns_empty_rotation() -> None:
    result = await _service(MemoryQuantRepository()).rotation()

    assert result.sectors == [] and result.data_through is None


# ---------------------------------------------------------------- intraday writer
async def test_store_intraday_snapshots_is_an_idempotent_upsert() -> None:
    repo = MemoryQuantRepository()
    slot = floor_to_slot(datetime(2026, 11, 2, 2, 37, 41, tzinfo=UTC))
    assert slot == datetime(2026, 11, 2, 2, 30, tzinfo=UTC)

    def row(price: float) -> IntradaySnapshot:
        return IntradaySnapshot(
            symbol="2330.TW",
            taken_at=slot,
            price=price,
            change_pct=0.01,
            volume_lots=10,
            amount_twd=1e6,
            available_at=slot,
        )

    assert await store_intraday_snapshots([row(100), row(101)], repo) == 1  # last row wins
    assert await store_intraday_snapshots([row(102)], repo) == 1
    assert len(repo.intraday_snapshots) == 1
    assert repo.intraday_snapshots[("2330.TW", slot)].price == 102
    assert await store_intraday_snapshots([], repo) == 0

    far_future = slot + timedelta(days=40)
    assert await prune_intraday_snapshots(repo, keep_days=30, now=far_future) == 1


def test_intraday_snapshot_requires_timezone_aware_timestamps() -> None:
    with pytest.raises(ValueError):
        IntradaySnapshot(
            symbol="2330.TW",
            taken_at=datetime(2026, 11, 2, 10, 30),
            price=1,
            change_pct=0,
            volume_lots=0,
            amount_twd=0,
            available_at=datetime(2026, 11, 2, 10, 30, tzinfo=UTC),
        )


# ----------------------------------------------------------------- industry sync
TWSE_SAMPLE = [
    {"出表日期": "1151125", "公司代號": "1101", "公司簡稱": "台泥", "產業別": "01"},
    {"出表日期": "1151125", "公司代號": "2330", "公司簡稱": "台積電", "產業別": "24"},
    {"出表日期": "1151125", "公司代號": "9110", "公司簡稱": "越南控", "產業別": "91"},
    {"出表日期": "1151125", "公司代號": "00XX", "公司簡稱": "bad", "產業別": "24"},
    {"出表日期": "1151125", "公司代號": "2317", "公司簡稱": "鴻海", "產業別": ""},
]
TPEX_SAMPLE = [
    {"Date": "1151125", "SecuritiesCompanyCode": "6488", "SecuritiesIndustryCode": "24"},
    {"Date": "1151125", "SecuritiesCompanyCode": "1240", "SecuritiesIndustryCode": "33"},
    {"Date": "1151125", "SecuritiesCompanyCode": "9999", "SecuritiesIndustryCode": "77"},
]


def test_parse_official_industry_payloads() -> None:
    at = datetime(2026, 11, 26, tzinfo=UTC)
    twse = {r.symbol: r for r in parse_twse_industries(TWSE_SAMPLE, available_at=at)}
    tpex = {r.symbol: r for r in parse_tpex_industries(TPEX_SAMPLE, available_at=at)}

    assert set(twse) == {"1101.TW", "2330.TW", "9110.TW"}
    assert twse["2330.TW"].industry_name == "半導體業"
    assert twse["1101.TW"].industry_name == "水泥工業"
    assert tpex["6488.TWO"].market == "TWO" and tpex["6488.TWO"].industry_name == "半導體業"
    assert tpex["1240.TWO"].industry_name == "農業科技業"
    assert tpex["9999.TWO"].industry_name == "其他(77)"
    with pytest.raises(ValueError):
        parse_twse_industries({"unexpected": 1}, available_at=at)


class _Provider:
    def __init__(self, payload: list[dict[str, str]]) -> None:
        self.calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            return httpx.Response(200, json=payload)

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_refresh_industries_runs_once_per_week_and_rejects_partial_data() -> None:
    repo = MemoryQuantRepository()
    big_twse = [
        {"公司代號": f"{1000 + i}", "產業別": "24"} for i in range(300)
    ]
    big_tpex = [
        {"SecuritiesCompanyCode": f"{6000 + i}", "SecuritiesIndustryCode": "22"}
        for i in range(300)
    ]
    twse, tpex = _Provider(big_twse), _Provider(big_tpex)
    monday = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)

    assert await refresh_industries(repo, twse, tpex, now=monday) == 600
    assert await refresh_industries(repo, twse, tpex, now=monday + timedelta(days=3)) == 0
    assert twse.calls == 1
    assert await refresh_industries(repo, twse, tpex, now=monday + timedelta(days=7)) == 600
    assert twse.calls == 2

    fresh = MemoryQuantRepository()
    with pytest.raises(RuntimeError):
        await refresh_industries(fresh, _Provider(big_twse[:10]), tpex, now=monday)
    assert fresh.market_industries == {} and fresh.fetch_coverage == {}


# -------------------------------------------------------------------------- REST
async def test_rest_sector_endpoints(repository) -> None:
    async def override():
        yield repository

    app.dependency_overrides[get_repository] = override
    try:
        with TestClient(app) as client:
            sectors = client.get("/api/v1/market/sectors", params={"window": 5})
            stocks = client.get("/api/v1/market/sectors/24/stocks", params={"window": 5})
            bad = client.get("/api/v1/market/sectors", params={"window": 7})
    finally:
        app.dependency_overrides.clear()

    assert sectors.status_code == 200
    body = sectors.json()
    assert {row["code"] for row in body["sectors"]} == {"24", "14"}
    assert body["intraday_status"] in {"unavailable", "not_applicable", "live"}
    assert stocks.status_code == 200
    assert [e["symbol"] for e in stocks.json()["entries"]][0] in {"2330.TW", "2454.TW"}
    assert bad.status_code == 422
