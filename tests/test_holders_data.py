from __future__ import annotations

import asyncio
import importlib.util
import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.adapters.rest.security import Principal, require_principal
from quant_signal.application import holders_data as sched
from quant_signal.application.official_queries import StockHoldersService
from quant_signal.application.stocks import StockNotFound
from quant_signal.domain.models import (
    ForeignHolding,
    MarketIndustry,
    ShareholdingDistribution,
)
from quant_signal.infrastructure.db.models import Base
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.infrastructure.providers import holders as src

FIXTURES = Path(__file__).parent / "fixtures" / "holders"
FETCHED = datetime(2026, 11, 28, 2, 0, tzinfo=UTC)
FAR = datetime(2027, 1, 1, tzinfo=UTC)
MARKETS = {
    "2317": "2317.TW",
    "2330": "2330.TW",
    "2454": "2454.TW",
    "1101": "1101.TW",
    "5388": "5388.TWO",
    "6488": "6488.TWO",
}


def tdcc_text() -> str:
    return (FIXTURES / "tdcc_sample.csv").read_text(encoding="utf-8")


def twse_payload() -> dict:
    return json.loads((FIXTURES / "twse_qfiis.json").read_text())


def tpex_payload() -> dict:
    return json.loads((FIXTURES / "tpex_qfii.json").read_text())


# --- TDCC parser -----------------------------------------------------------------------


def test_tdcc_parses_tiers_thresholds_and_symbols() -> None:
    rows = src.parse_tdcc_distribution(tdcc_text(), markets=MARKETS, available_at=FETCHED)
    by_symbol = {row.symbol: row for row in rows}
    # ETFs (0050, 00400A) and the bond-like 000218 are not in the symbol map.
    assert set(by_symbol) == {"1101.TW", "2317.TW", "2330.TW", "2454.TW", "5388.TWO", "6488.TWO"}

    hon_hai = by_symbol["2317.TW"]
    assert hon_hai.data_date == date(2026, 11, 20)
    assert hon_hai.total_holders == 1_139_408
    assert hon_hai.total_shares == 14_075_746_497
    # levels 12-15: > 400,000 shares
    assert hon_hai.holders_ge400 == 329 + 165 + 130 + 831
    assert hon_hai.pct_ge400 == pytest.approx(69.39, abs=0.01)
    assert hon_hai.holders_ge1000 == 831
    assert hon_hai.pct_ge1000 == pytest.approx(66.60, abs=0.01)
    # levels 1-3: 10 lots or fewer
    assert hon_hai.retail_holders == 433_565 + 544_014 + 83_452
    assert hon_hai.retail_pct == pytest.approx(0.70 + 8.00 + 4.43, abs=0.05)
    # raw tiers 1-16 kept, 17 (合計) only as the totals
    assert [t.level for t in hon_hai.tiers] == list(range(1, 17))
    assert hon_hai.tiers[15].shares == 14_000  # 差異數調整
    assert hon_hai.source == "tdcc_opendata" and hon_hai.available_at == FETCHED


def test_tdcc_handles_bom_crlf_blank_lines_and_odd_rows() -> None:
    text = tdcc_text()
    assert text.startswith("﻿") or "資料日期" in text
    messy = "﻿" + text.lstrip("﻿").replace("\r\n", "\r\n\r\n", 3)
    messy += "\r\n20261120,2330  ,x,1,2,3\r\n"  # garbage level -> drops 2330 entirely
    messy += "20261120,9999  ,17,5,5000,100.00\r\n"  # unknown code
    messy += "garbage\r\n"
    rows = {
        r.symbol for r in src.parse_tdcc_distribution(messy, markets=MARKETS, available_at=FETCHED)
    }
    assert "2330.TW" not in rows and "2317.TW" in rows and "9999.TW" not in rows


def test_tdcc_drops_security_without_total_row_and_accepts_roc_or_slash_dates() -> None:
    partial = "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\r\n"
    partial += "20261120,1101  ,15,3,3000000,100.00\r\n"  # no level 17
    partial += '1151120,2330  ,15,"1,000","3,000,000",100.00\r\n'  # ROC date, commas
    partial += '1151120,2330  ,17,"1,000","3,000,000",100.00\r\n'
    rows = src.parse_tdcc_distribution(partial, markets=MARKETS, available_at=FETCHED)
    assert [r.symbol for r in rows] == ["2330.TW"]
    assert rows[0].data_date == date(2026, 11, 20)
    assert rows[0].total_shares == 3_000_000 and rows[0].pct_ge1000 == 100.0
    assert rows[0].retail_holders == 0


# --- foreign holding parsers -------------------------------------------------------------


def test_twse_qfiis_parser() -> None:
    rows = {r.symbol: r for r in src.parse_twse_qfiis(twse_payload(), available_at=FETCHED)}
    # ETFs (0050, 00400A, 00419A) are dropped; common stocks kept
    assert set(rows) == {"1101.TW", "2317.TW", "2330.TW", "2454.TW"}
    tsmc = rows["2330.TW"]
    assert tsmc.trading_date == date(2026, 11, 26)
    assert tsmc.shares_outstanding == 25_932_370_067  # "25,932,370,067"
    assert tsmc.foreign_shares == 17_526_865_407
    assert tsmc.foreign_holding_pct == pytest.approx(67.58)
    assert tsmc.source == "twse_qfiis"


def test_twse_qfiis_non_ok_and_bad_payloads_yield_nothing() -> None:
    assert src.parse_twse_qfiis({"stat": "查詢日期大於可查詢最大日期"}, available_at=FETCHED) == []
    assert src.parse_twse_qfiis([], available_at=FETCHED) == []
    broken = twse_payload()
    broken["fields"] = ["a", "b"]
    with pytest.raises(ValueError):
        src.parse_twse_qfiis(broken, available_at=FETCHED)


def test_tpex_qfii_parser_uses_roc_date_and_percent_strings() -> None:
    rows = {r.symbol: r for r in src.parse_tpex_qfii(tpex_payload(), available_at=FETCHED)}
    assert set(rows) == {"8455.TWO", "5276.TWO", "6629.TWO", "2035.TWO", "6856.TWO", "6488.TWO"}
    row = rows["6488.TWO"]
    assert row.trading_date == date(2026, 11, 26)
    assert row.shares_outstanding == 528_113_725
    assert row.foreign_shares == 162_697_999
    assert row.foreign_holding_pct == pytest.approx(30.8)
    assert rows["6856.TWO"].foreign_shares == 1000
    empty = {"tables": [{"date": "115/11/28", "totalCount": 0, "data": []}], "stat": "ok"}
    assert src.parse_tpex_qfii(empty, available_at=FETCHED) == []


def test_request_params() -> None:
    assert src.twse_qfiis_params(date(2026, 11, 26)) == {
        "date": "20261126",
        "selectType": "ALLBUT0999",
        "response": "json",
    }
    assert src.tpex_qfii_params(date(2026, 1, 5))["d"] == "115/01/05"


# --- storage and reads -----------------------------------------------------------------


def week(symbol: str, data_date: date, pct400: float, *, at: datetime) -> ShareholdingDistribution:
    return ShareholdingDistribution(
        symbol=symbol,
        data_date=data_date,
        total_holders=1000,
        total_shares=10_000_000,
        holders_ge400=10,
        pct_ge400=pct400,
        holders_ge1000=5,
        pct_ge1000=pct400 / 2,
        retail_holders=800,
        retail_pct=10.0,
        source="tdcc_opendata",
        available_at=at,
    )


async def test_upsert_is_idempotent_and_reads_are_point_in_time() -> None:
    repo = MemoryQuantRepository()
    at = datetime(2026, 11, 28, 2, tzinfo=UTC)
    rows = [week("2330.TW", date(2026, 11, 20), 70.0, at=at)]
    assert await repo.upsert_shareholding_distribution(rows) == 1
    assert await repo.upsert_shareholding_distribution(rows) == 0
    before = at - timedelta(days=1)
    assert await repo.latest_shareholding_pairs(before) == {}
    assert (await repo.shareholding_history("2330.TW", before)) == []


async def test_pairs_prefer_four_weeks_back_else_previous_week() -> None:
    repo = MemoryQuantRepository()
    at = datetime(2026, 12, 20, tzinfo=UTC)
    fridays = [date(2026, 11, 20) - timedelta(weeks=n) for n in range(6)]  # newest first
    await repo.upsert_shareholding_distribution(
        [week("2330.TW", d, 70.0 - i, at=at) for i, d in enumerate(fridays)]
        + [week("2317.TW", d, 50.0, at=at) for d in fridays[:2]]  # only 2 weeks of history
        + [week("2454.TW", fridays[0], 40.0, at=at)]  # a single week
    )
    pairs = await repo.latest_shareholding_pairs(at)
    tsmc = pairs["2330.TW"]
    assert tsmc.latest.data_date == date(2026, 11, 20)
    assert tsmc.prior is not None and tsmc.prior.data_date == date(2026, 10, 23)  # 4 weeks back
    assert pairs["2317.TW"].prior.data_date == date(2026, 11, 13)  # previous available
    assert pairs["2454.TW"].prior is None
    assert not hasattr(tsmc.latest, "tiers")  # lean


async def test_latest_foreign_holdings_take_newest_day_per_symbol() -> None:
    repo = MemoryQuantRepository()

    def held(symbol: str, day: date, pct: float, at: datetime) -> ForeignHolding:
        return ForeignHolding(
            symbol=symbol,
            trading_date=day,
            shares_outstanding=1000,
            foreign_shares=int(pct * 10),
            foreign_holding_pct=pct,
            source="twse_qfiis",
            available_at=at,
        )

    early, late = datetime(2026, 11, 25, 12, tzinfo=UTC), datetime(2026, 11, 26, 12, tzinfo=UTC)
    rows = [
        held("2330.TW", date(2026, 11, 24), 60.0, early),
        held("2330.TW", date(2026, 11, 25), 61.0, late),
        held("2317.TW", date(2026, 11, 24), 39.0, early),
    ]
    assert await repo.upsert_foreign_holding(rows) == 3
    assert await repo.upsert_foreign_holding(rows) == 0
    now = await repo.latest_foreign_holdings(late)
    assert now["2330.TW"].foreign_holding_pct == 61.0 and now["2317.TW"].foreign_holding_pct == 39.0
    then = await repo.latest_foreign_holdings(early)
    assert then["2330.TW"].foreign_holding_pct == 60.0  # the 25th was not yet fetched
    assert (await repo.latest_foreign_holding("2330.TW", late)).trading_date == date(2026, 11, 25)
    assert await repo.latest_foreign_holding("9999.TW", late) is None


# --- scheduler -------------------------------------------------------------------------


class FakeExchanges:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.requests: Counter[str] = Counter()
        self.fail = fail or set()
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        key = f"{url.host}{url.path}"
        self.requests[key] += 1
        if key in self.fail:
            return httpx.Response(500)
        if url.host == "opendata.tdcc.com.tw":
            return httpx.Response(200, content=b"\xef\xbb\xbf" + tdcc_text().encode())
        if url.host == "www.twse.com.tw":
            return httpx.Response(200, json=twse_payload())
        return httpx.Response(200, json=tpex_payload())


TDCC_KEY = "opendata.tdcc.com.tw/getOD.ashx"
TWSE_KEY = "www.twse.com.tw/rwd/zh/fund/MI_QFIIS"
TPEX_KEY = "www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(sched, "MIN_TDCC_ROWS", 1)
    monkeypatch.setattr(sched, "MIN_FOREIGN_ROWS", 1)


def taipei(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC) - timedelta(hours=8)


async def seeded_repo() -> MemoryQuantRepository:
    repo = MemoryQuantRepository()
    await repo.upsert_market_industries(
        [
            MarketIndustry(
                symbol=symbol,
                market="TWO" if symbol.endswith(".TWO") else "TW",
                industry_code="24",
                industry_name="半導體業",
                source="twse_openapi",
                available_at=FETCHED,
            )
            for symbol in MARKETS.values()
        ]
    )
    return repo


def test_slots_and_window() -> None:
    assert sched.tdcc_slot(taipei(28, 7, 59)) is None  # Saturday early morning
    assert sched.tdcc_slot(taipei(28, 8)) == "2026-11-28-am"
    assert sched.tdcc_slot(taipei(28, 17)) == "2026-11-28-pm"
    # Friday 16:00: today is not published yet; 17:00 it counts
    assert sched.foreign_days(taipei(27, 16), 3) == [
        date(2026, 11, 24),
        date(2026, 11, 25),
        date(2026, 11, 26),
    ]
    assert sched.foreign_days(taipei(27, 17), 2) == [date(2026, 11, 26), date(2026, 11, 27)]
    # weekend: last two weekdays
    assert sched.foreign_days(taipei(29, 12), 2) == [date(2026, 11, 26), date(2026, 11, 27)]


async def test_refresh_stores_both_datasets_and_is_idempotent() -> None:
    repo, exchange = await seeded_repo(), FakeExchanges()
    now = taipei(27, 18)
    written = await sched.refresh_holders_data(
        repo, exchange, exchange, now=now, attempts={}, request_interval=0
    )
    assert written["tdcc_shareholding"] == 6
    assert written["foreign_holding"] > 0
    assert len(await repo.latest_shareholding_pairs(FAR)) == 6
    # 11-23..25 are empty (recorded), 26 stored, today (27) not published -> retry later
    coverage = await repo.list_fetch_coverage(
        sched.SOURCE, sched.FOREIGN_HOLDING, "*", [f"2026-11-{d}" for d in (23, 24, 25, 26, 27)]
    )
    assert set(coverage) == {"2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26"}
    assert coverage["2026-11-25"].status == "empty" and coverage["2026-11-26"].status == "ok"

    exchange.requests.clear()
    again = await sched.refresh_holders_data(
        repo, exchange, exchange, now=now, attempts={}, request_interval=0
    )
    assert TDCC_KEY not in exchange.requests  # same slot already recorded
    assert again.get("tdcc_shareholding") is None
    # only the still-missing day is asked for again
    assert exchange.requests[TWSE_KEY] == 1 and exchange.requests[TPEX_KEY] == 1


async def test_next_slot_refetches_tdcc_but_adds_nothing() -> None:
    repo, exchange = await seeded_repo(), FakeExchanges()
    await sched.refresh_holders_data(
        repo, exchange, exchange, now=taipei(28, 9), attempts={}, request_interval=0
    )
    written = await sched.refresh_holders_data(
        repo, exchange, exchange, now=taipei(28, 18), attempts={}, request_interval=0
    )
    assert written["tdcc_shareholding"] == 0  # same data_date: nothing new
    assert len(await repo.latest_shareholding_pairs(FAR)) == 6


async def test_failure_is_retried_later_and_never_raises() -> None:
    repo, exchange = await seeded_repo(), FakeExchanges(fail={TDCC_KEY, TWSE_KEY})
    attempts: dict[str, datetime] = {}
    now = taipei(27, 18)
    written = await sched.refresh_holders_data(
        repo, exchange, exchange, now=now, attempts=attempts, fetch_attempts=2, request_interval=0
    )
    assert "tdcc_shareholding" not in written
    assert set(attempts) == {"tdcc_shareholding", "foreign_holding"}
    assert not await repo.latest_shareholding_pairs(FAR)
    # TPEx side of the first day was still stored, but nothing was marked done
    assert (
        await repo.list_fetch_coverage(sched.SOURCE, sched.FOREIGN_HOLDING, "*", ["2026-11-23"])
        == {}
    )

    exchange.requests.clear()
    await sched.refresh_holders_data(
        repo,
        exchange,
        exchange,
        now=now + timedelta(minutes=5),
        attempts=attempts,
        request_interval=0,
    )
    assert not exchange.requests  # inside the retry window

    exchange.fail.clear()
    await sched.refresh_holders_data(
        repo,
        exchange,
        exchange,
        now=now + timedelta(minutes=11),
        attempts=attempts,
        request_interval=0,
    )
    assert (
        len(await repo.latest_shareholding_pairs(FAR)) == 6 and "tdcc_shareholding" not in attempts
    )


async def test_tdcc_needs_industry_classification_first() -> None:
    repo, exchange = MemoryQuantRepository(), FakeExchanges()
    attempts: dict[str, datetime] = {}
    await sched.refresh_holders_data(
        repo,
        exchange,
        exchange,
        now=taipei(28, 9),
        attempts=attempts,
        foreign_enabled=False,
        request_interval=0,
    )
    assert "tdcc_shareholding" in attempts and not await repo.latest_shareholding_pairs(FAR)
    assert TDCC_KEY not in exchange.requests


async def test_flags_disable_datasets() -> None:
    repo, exchange = await seeded_repo(), FakeExchanges()
    written = await sched.refresh_holders_data(
        repo, exchange, exchange, now=taipei(27, 18), tdcc_enabled=False, foreign_enabled=False
    )
    assert written == {} and not exchange.requests


# --- API -------------------------------------------------------------------------------


@pytest.fixture
async def api_repo() -> MemoryQuantRepository:
    repo = await seeded_repo()
    exchange = FakeExchanges()
    await sched.refresh_holders_data(
        repo, exchange, exchange, now=taipei(27, 18), attempts={}, request_interval=0
    )
    return repo


async def test_holders_service_series_and_foreign(api_repo) -> None:
    result = await StockHoldersService(api_repo).get("2330.TW", as_of=date(2026, 11, 28))
    assert result.symbol == "2330.TW"
    assert [p.data_date for p in result.series] == [date(2026, 11, 20)]
    assert result.shares_outstanding == 25_932_370_067
    assert result.foreign_holding_pct == pytest.approx(67.58)
    assert result.foreign_data_date == date(2026, 11, 26)
    early = await StockHoldersService(api_repo).get("2330.TW", as_of=date(2026, 11, 20))
    assert early.series == [] and early.shares_outstanding is None  # fetched on the 27th
    with pytest.raises(StockNotFound):
        await StockHoldersService(api_repo).get("9998", as_of=date(2026, 11, 28))


def test_holders_endpoint(api_repo) -> None:
    app.dependency_overrides[get_repository] = lambda: api_repo
    app.dependency_overrides[require_principal] = lambda: Principal("anonymous")
    try:
        client = TestClient(app)
        response = client.get("/api/v1/stocks/2330.TW/holders?as_of=2026-11-28")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["series"][0]["pct_ge1000"] > 0 and body["foreign_holding_pct"] == 67.58
        assert client.get("/api/v1/stocks/9998/holders").status_code == 404
    finally:
        app.dependency_overrides.clear()


# --- migration and Postgres statements ----------------------------------------------------

MIGRATION = Path(__file__).parents[1] / "migrations" / "versions" / "0017_holders_data.py"
TABLES = ("shareholding_distribution_weekly", "foreign_holding_daily")


class _Recorder:
    def __init__(self) -> None:
        self.tables: dict[str, tuple] = {}
        self.indexes: dict[str, tuple] = {}
        self.dropped: list[str] = []

    def create_table(self, name, *items):
        self.tables[name] = items

    def create_index(self, name, table, columns):
        self.indexes[name] = (table, tuple(columns))

    def drop_index(self, name, table_name):
        self.indexes.pop(name, None)

    def drop_table(self, name):
        self.dropped.append(name)


@pytest.fixture
def migration():
    spec = importlib.util.spec_from_file_location("migration_0017", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    recorder = _Recorder()
    module.op = recorder
    return module, recorder


def test_migration_matches_orm(migration) -> None:
    module, recorder = migration
    assert module.revision == "0017_holders_data"
    module.upgrade()
    assert set(recorder.tables) == set(TABLES)
    for name in TABLES:
        orm = Base.metadata.tables[name]
        items = recorder.tables[name]
        columns = {i.name: i for i in items if isinstance(i, sa.Column)}
        assert set(columns) == {c.name for c in orm.columns}, name
        for column in orm.columns:
            assert type(columns[column.name].type) is type(column.type), (name, column.name)
        pk = next(i for i in items if isinstance(i, sa.PrimaryKeyConstraint))
        assert list(pk._pending_colargs) == [c.name for c in orm.primary_key.columns], name
    orm_indexes = {
        index.name: (table.name, tuple(c.name for c in index.columns))
        for table in (Base.metadata.tables[n] for n in TABLES)
        for index in table.indexes
    }
    assert recorder.indexes == orm_indexes
    module.downgrade()
    assert set(recorder.dropped) == set(TABLES) and recorder.indexes == {}


class _Result:
    rowcount = 0

    def scalar(self):
        return date(2026, 11, 20)

    def scalars(self):
        return self

    def mappings(self):
        return iter(())

    def first(self):
        return None

    def all(self):
        return []

    def __iter__(self):
        return iter(())


class _StubSession:
    def __init__(self) -> None:
        self.sql: list[str] = []

    async def execute(self, statement):
        from sqlalchemy.dialects import postgresql

        self.sql.append(str(statement.compile(dialect=postgresql.dialect())))
        return _Result()

    async def commit(self) -> None:
        pass


async def test_postgres_statements_compile_with_point_in_time_filters() -> None:
    from quant_signal.infrastructure.db.repository import PostgresQuantRepository

    session = _StubSession()
    repo = PostgresQuantRepository(session)  # type: ignore[arg-type]
    cutoff = FETCHED
    await repo.latest_shareholding_pairs(cutoff)
    await repo.latest_foreign_holdings(cutoff)
    await repo.latest_foreign_holding("2330.TW", cutoff)
    await repo.shareholding_history("2330.TW", cutoff)
    await repo.upsert_shareholding_distribution(
        [week("2330.TW", date(2026, 11, 20), 70.0, at=cutoff)]
    )
    assert all("available_at <=" in sql for sql in session.sql[:5])
    assert any("row_number()" in sql for sql in session.sql)
    assert any("ON CONFLICT DO NOTHING" in sql for sql in session.sql)
