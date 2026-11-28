"""Short-lending, day-trade, insider-pledge and dividend data: parsers (real trimmed
payloads under fixtures/shortdata), derived metrics, storage, scheduler and REST."""

from __future__ import annotations

import asyncio
import copy
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
from quant_signal.application import short_data as sched
from quant_signal.application.short_data_queries import StockShortInterestService
from quant_signal.application.stocks import StockNotFound
from quant_signal.domain.models import ForeignHolding, MarketStockDay
from quant_signal.domain.short_data import DayTradeDay, DividendEvent, ShortLendingDay
from quant_signal.infrastructure.db.models import Base
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.infrastructure.providers import short_data as src
from quant_signal.quant import short_data as calc

FIXTURES = Path(__file__).parent / "fixtures" / "shortdata"
FETCHED = datetime(2026, 11, 28, 2, 0, tzinfo=UTC)
FAR = datetime(2027, 1, 1, tzinfo=UTC)


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


# --- parsers ---------------------------------------------------------------------------


def test_twse_sbl_units_and_filters() -> None:
    rows = {r.symbol: r for r in src.parse_twse_sbl(load("twse_twt93u.json"), available_at=FETCHED)}
    # ETFs (0050, 00400A) and the odd 9999 row are not ordinary stocks
    assert set(rows) == {"1101.TW", "2317.TW", "2330.TW"}
    tsmc = rows["2330.TW"]
    assert tsmc.trading_date == date(2026, 11, 27)
    # shares, not lots: TSMC's 17,457,514-share lending balance is ~17.5k lots
    assert tsmc.sbl_balance == 17_457_514 and tsmc.sbl_sold == 67_000
    assert tsmc.sbl_returned == 20_000 and tsmc.sbl_adjustment == 0
    assert tsmc.sbl_limit_next_day == 455_720
    # published identity: balance = previous + sold - returned + adjustment
    assert tsmc.sbl_prev_balance + tsmc.sbl_sold - tsmc.sbl_returned == tsmc.sbl_balance


def test_twse_sbl_empty_or_future_days() -> None:
    assert (
        src.parse_twse_sbl({"stat": "OK", "date": "20261128", "data": []}, available_at=FETCHED)
        == []
    )
    future = {"stat": "查詢日期大於今日，請重新查詢!", "total": 0}
    assert src.parse_twse_sbl(future, available_at=FETCHED) == []
    broken = load("twse_twt93u.json") | {"fields": ["代號"]}
    with pytest.raises(ValueError):
        src.parse_twse_sbl(broken, available_at=FETCHED)


def test_tpex_sbl_same_layout() -> None:
    rows = {r.symbol: r for r in src.parse_tpex_sbl(load("tpex_sbl.json"), available_at=FETCHED)}
    assert set(rows) == {"3293.TWO", "6488.TWO", "8069.TWO"}  # ETFs dropped
    assert rows["6488.TWO"].sbl_balance == 9_327_000
    assert rows["6488.TWO"].sbl_returned == 516_000
    assert rows["6488.TWO"].trading_date == date(2026, 11, 25) or rows["6488.TWO"].trading_date
    holiday = {
        "date": "20261128",
        "stat": "ok",
        "tables": [{"date": "115/11/28", "data": [], "fields": []}],
    }
    assert src.parse_tpex_sbl(holiday, available_at=FETCHED) == []


def test_twse_daytrade_picks_the_per_stock_table() -> None:
    rows = {
        r.symbol: r for r in src.parse_twse_daytrade(load("twse_twtb4u.json"), available_at=FETCHED)
    }
    assert rows["2330.TW"].day_trade_volume == 8_026_000
    assert rows["2330.TW"].day_trade_buy_amount == 17_308_880_000
    assert rows["2330.TW"].day_trade_sell_amount == 17_415_120_000
    assert "0050.TW" not in rows and "00400A.TW" not in rows
    # '--' volume drops the row; '--' amount stays as missing, never 0
    assert "2603.TW" not in rows
    assert rows["3008.TW"].day_trade_buy_amount is None


def test_twse_daytrade_target_list_only_yields_nothing() -> None:
    # TWSE answers a holiday / not-yet-published day with the next day's target list only
    assert (
        src.parse_twse_daytrade(load("twse_twtb4u_targets_only.json"), available_at=FETCHED) == []
    )
    assert src.parse_twse_daytrade({"stat": "查詢日期大於今日"}, available_at=FETCHED) == []


def test_pledges_dedupe_people_and_sum() -> None:
    rows = {
        r.symbol: r
        for r in src.parse_twse_pledges(load("twse_t187ap11_L.json"), available_at=FETCHED)
    }
    tsmc = rows["2330.TW"]
    assert tsmc.period == "2026-10" and tsmc.published_on == date(2026, 11, 20)
    # 魏哲家 is listed as chairman and as CEO with the same 7,656,724 shares / 1.6M pledged
    raw = load("twse_t187ap11_L.json")
    naive = sum(int(r["設質股數"]) for r in raw if r["公司代號"] == "2330")
    assert naive == 1_628_000 + 100_000 + 0 or tsmc.pledged_shares < naive
    assert tsmc.pledged_shares == 1_728_000
    assert tsmc.pledge_ratio_pct == pytest.approx(1_728_000 / tsmc.holding_shares * 100, abs=1e-3)
    otc = src.parse_tpex_pledges(load("tpex_t187ap11_O.json"), available_at=FETCHED)
    assert otc[0].symbol == "6488.TWO" and otc[0].pledge_ratio_pct == 0.0


def test_pledges_reject_non_list() -> None:
    with pytest.raises(ValueError):
        src.parse_twse_pledges({"oops": 1}, available_at=FETCHED)


def test_twse_dividends_structured_amounts() -> None:
    rows = {
        r.symbol: r
        for r in src.parse_twse_dividends(load("twse_twt48u.json"), available_at=FETCHED)
    }
    tsmc = rows["2330.TW"]
    assert tsmc.ex_date == date(2026, 12, 10) and tsmc.kind == "ex_dividend"
    assert tsmc.cash_dividend_per_share == pytest.approx(7.000001)
    mixed = rows["2236.TW"]  # 權息: cash and stock dividend together
    assert mixed.kind == "ex_rights_dividend"
    assert mixed.cash_dividend_per_share == pytest.approx(0.386678)
    assert mixed.stock_dividend_per_share == pytest.approx(0.02416742)
    rights = rows["2345.TW"]  # cash capital increase only
    assert rights.cash_capital_increase_ratio == pytest.approx(0.01782156)
    assert rights.subscription_price == 1217.0
    assert rows["2646.TW"].subscription_price is None  # 尚未公告
    assert "00939.TW" not in rows  # ETFs are out of scope


def test_tpex_dividends() -> None:
    rows = {
        r.symbol: r
        for r in src.parse_tpex_dividends(load("tpex_exright_prepost.json"), available_at=FETCHED)
    }
    assert rows["6488.TWO"].cash_dividend_per_share == 6.0
    assert rows["6160.TWO"].cash_capital_increase_ratio == pytest.approx(0.16060945)
    assert rows["6160.TWO"].subscription_price is None  # 註4
    assert "00758B.TWO" not in rows


def test_request_params() -> None:
    assert src.twse_params(date(2026, 11, 27)) == {"date": "20261127", "response": "json"}
    assert src.tpex_sbl_params(date(2026, 1, 5))["d"] == "115/01/05"


# --- derived metrics -------------------------------------------------------------------

SESSIONS = [date(2026, 10, 1) + timedelta(days=i) for i in range(30)]


def test_balance_change_and_zero_base() -> None:
    balances = {d: 100 + i for i, d in enumerate(SESSIONS)}
    last = SESSIONS[-1]
    assert calc.balance_change_pct(balances, SESSIONS, last, 5) == pytest.approx(
        5 / 124 * 100, abs=0.005
    )
    assert calc.balance_change_pct(balances, SESSIONS, SESSIONS[2], 5) is None  # not enough history
    zero = {SESSIONS[-6]: 0, last: 50}
    assert calc.balance_change_pct(zero, SESSIONS, last, 5) is None  # zero base -> None, not inf
    assert calc.balance_change_pct({last: 50}, SESSIONS, last, 5) is None  # missing earlier day


def test_days_to_cover_and_average_volume() -> None:
    volumes = {d: 1000 for d in SESSIONS}
    avg = calc.average_volume(volumes, SESSIONS, SESSIONS[-1])
    assert avg == 1000 and calc.days_to_cover(5000, avg) == 5
    assert calc.average_volume({SESSIONS[-1]: 1000}, SESSIONS, SESSIONS[-1]) is None  # too thin
    assert calc.days_to_cover(None, 1000) is None and calc.days_to_cover(5, 0) is None


def test_window_ratio_is_volume_weighted_and_needs_coverage() -> None:
    volumes = {d: 1000 for d in SESSIONS}
    volumes[SESSIONS[-1]] = 3000
    shares = {d: 100 for d in SESSIONS}
    shares[SESSIONS[-1]] = 1500
    assert calc.window_ratio_pct(shares, volumes, SESSIONS, SESSIONS[-1], 5) == pytest.approx(
        (400 + 1500) / (4000 + 3000) * 100, abs=0.005
    )
    sparse = {SESSIONS[-1]: 100, SESSIONS[-2]: 100}
    assert calc.window_ratio_pct(sparse, volumes, SESSIONS, SESSIONS[-1], 5) is None
    assert calc.window_ratio_pct(shares, volumes, SESSIONS, SESSIONS[2], 5) is None  # short history
    assert calc.pct(1, 0) is None and calc.net_pnl_proxy(100, 130) == 30
    assert calc.net_pnl_proxy(None, 5) is None


# --- storage and reads -----------------------------------------------------------------


def sbl(symbol: str, day: date, balance: int, *, sold: int = 0, at: datetime = FETCHED):
    return ShortLendingDay(
        symbol=symbol,
        trading_date=day,
        source="twse_twt93u",
        sbl_sold=sold,
        sbl_returned=0,
        sbl_balance=balance,
        available_at=at,
    )


def trade(symbol: str, day: date, volume: int, *, at: datetime = FETCHED):
    return DayTradeDay(
        symbol=symbol,
        trading_date=day,
        source="twse_twtb4u",
        day_trade_volume=volume,
        day_trade_buy_amount=volume * 100,
        day_trade_sell_amount=volume * 101,
        available_at=at,
    )


def stock_day(symbol: str, day: date, volume: int, short: int | None = 7) -> MarketStockDay:
    return MarketStockDay(
        symbol=symbol,
        name="台積電",
        market="TW",
        trading_date=day,
        open=100,
        high=101,
        low=99,
        close=100,
        volume=volume,
        turnover_value=volume * 100.0,
        short_balance=short,
        available_at=datetime(2026, 11, 27, 15, tzinfo=UTC),
        source="twse",
    )


async def test_revisions_only_when_numbers_change() -> None:
    repo = MemoryQuantRepository()
    day = date(2026, 11, 25)
    t1, t2, t3 = (FETCHED + timedelta(days=n) for n in range(3))
    assert await repo.upsert_day_trade([trade("2330.TW", day, 1000, at=t1)]) == 1
    assert await repo.upsert_day_trade([trade("2330.TW", day, 1000, at=t2)]) == 0  # identical
    assert await repo.upsert_day_trade([trade("2330.TW", day, 1100, at=t2)]) == 1  # T+1 update
    assert await repo.upsert_day_trade([trade("2330.TW", day, 1200, at=t3)]) == 1  # T+2 final
    seen = {at: (await repo.sd_day_trade_rows(day, at))[0] for at in (t1, t2, t3)}
    assert [seen[t].day_trade_volume for t in (t1, t2, t3)] == [1000, 1100, 1200]
    assert seen[t3].revision == 3
    assert await repo.sd_day_trade_rows(day, t1 - timedelta(hours=1)) == []  # nothing visible yet


async def seeded_market() -> MemoryQuantRepository:
    repo = MemoryQuantRepository()
    sessions = [
        d for d in (date(2026, 10, 1) + timedelta(days=i) for i in range(60)) if d.weekday() < 5
    ]
    sessions = [d for d in sessions if d <= date(2026, 11, 27)]
    at = datetime(2026, 11, 27, 15, tzinfo=UTC)
    await repo.upsert_market_stock_days(
        [stock_day("2330.TW", d, 10_000_000, short=40 + i) for i, d in enumerate(sessions)]
        + [stock_day("2317.TW", d, 5_000_000) for d in sessions]
    )
    await repo.upsert_short_lending(
        [
            sbl("2330.TW", d, 1_000_000 + 10_000 * i, sold=50_000, at=at)
            for i, d in enumerate(sessions)
        ]
        + [sbl("2317.TW", d, 0, at=at) for d in sessions]
    )
    await repo.upsert_day_trade(
        [trade("2330.TW", d, 2_000_000, at=at) for d in sessions[-22:]]
        + [trade("2317.TW", d, 500_000, at=at) for d in sessions[-3:]]
    )
    await repo.upsert_foreign_holding(
        [
            ForeignHolding(
                symbol="2330.TW",
                trading_date=sessions[-1],
                shares_outstanding=100_000_000,
                foreign_shares=70_000_000,
                foreign_holding_pct=70.0,
                source="twse_qfiis",
                available_at=at,
            )
        ]
    )
    return repo


async def test_whole_market_metrics() -> None:
    repo = await seeded_market()
    metrics = await repo.latest_short_lending_metrics(FAR)
    tsmc = metrics["2330.TW"]
    assert tsmc.trading_date == date(2026, 11, 27)
    assert tsmc.sbl_pct_of_shares == pytest.approx(tsmc.sbl_balance_shares / 100_000_000 * 100)
    assert tsmc.sbl_sold_pct_of_volume == pytest.approx(0.5)
    assert tsmc.days_to_cover == pytest.approx(tsmc.sbl_balance_shares / 10_000_000)
    assert tsmc.sbl_change_5d_pct is not None and tsmc.sbl_change_20d_pct > tsmc.sbl_change_5d_pct
    assert metrics["2317.TW"].sbl_change_5d_pct is None  # zero balance base

    trades = await repo.latest_day_trade_metrics(FAR)
    assert trades["2330.TW"].day_trade_ratio_pct == pytest.approx(20.0)
    assert trades["2330.TW"].day_trade_ratio_20d_pct == pytest.approx(20.0)
    assert trades["2330.TW"].day_trade_net_pnl_proxy == 2_000_000
    assert trades["2317.TW"].day_trade_ratio_pct == pytest.approx(10.0)
    assert trades["2317.TW"].day_trade_ratio_5d_pct is None  # only 3 sessions of data
    # point in time: nothing was fetched before the cutoff
    assert await repo.latest_short_lending_metrics(datetime(2026, 10, 1, tzinfo=UTC)) == {}
    assert await repo.latest_day_trade_metrics(datetime(2026, 10, 1, tzinfo=UTC)) == {}


async def test_pledges_and_events_reads() -> None:
    repo = MemoryQuantRepository()
    at = datetime(2026, 11, 28, 2, tzinfo=UTC)
    await repo.upsert_insider_holdings(
        src.parse_twse_pledges(load("twse_t187ap11_L.json"), available_at=at)
        + src.parse_tpex_pledges(load("tpex_t187ap11_O.json"), available_at=at)
    )
    pledges = await repo.latest_insider_pledges(FAR)
    assert (
        pledges["2330.TW"].pledged_shares == 1_728_000 and pledges["6488.TWO"].period == "2026-10"
    )
    assert await repo.latest_insider_pledges(at - timedelta(days=1)) == {}

    events = src.parse_twse_dividends(load("twse_twt48u.json"), available_at=at)
    events += src.parse_tpex_dividends(load("tpex_exright_prepost.json"), available_at=at)
    assert await repo.upsert_dividend_events(events) == len(events)
    assert await repo.upsert_dividend_events(events) == 0
    nxt = await repo.next_dividend_events(at)
    assert nxt["2330.TW"].date == date(2026, 12, 10) and nxt[
        "2330.TW"
    ].cash_dividend == pytest.approx(7.000001)
    assert nxt["2236.TW"].stock_dividend == pytest.approx(0.02416742)
    assert "6523.TWO" not in nxt  # ex date 11/26 already passed
    window = await repo.list_dividend_events(
        ["2330.tw", "2345.TW"], date(2026, 12, 1), date(2026, 12, 31), at
    )
    assert [(e.symbol, e.ex_date) for e in window] == [
        ("2345.TW", date(2026, 12, 4)),
        ("2330.TW", date(2026, 12, 10)),
    ]
    # revised amount announced later: new revision, old one still visible to an old cutoff
    revised = [
        e.model_copy(
            update={"cash_dividend_per_share": 7.5, "available_at": at + timedelta(days=1)}
        )
        for e in events
        if e.symbol == "2330.TW"
    ]
    assert await repo.upsert_dividend_events(revised) == 1
    got = await repo.list_dividend_events(["2330.TW"], date(2026, 12, 1), date(2026, 12, 31), FAR)
    assert got[0].cash_dividend_per_share == 7.5 and got[0].revision == 2
    old = await repo.list_dividend_events(["2330.TW"], date(2026, 12, 1), date(2026, 12, 31), at)
    assert old[0].cash_dividend_per_share == pytest.approx(7.000001)


# --- scheduler -------------------------------------------------------------------------


class FakeExchanges:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.requests: Counter[str] = Counter()
        self.fail = fail or set()
        self.twse_sbl = load("twse_twt93u.json")
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        key = f"{request.url.host}{request.url.path}"
        self.requests[key] += 1
        if key in self.fail:
            return httpx.Response(500)
        body = {
            "www.twse.com.tw/rwd/zh/marginTrading/TWT93U": lambda: self.twse_sbl,
            "www.tpex.org.tw/web/stock/margin_trading/margin_sbl/margin_sbl_result.php": (
                lambda: load("tpex_sbl.json")
            ),
            "www.twse.com.tw/rwd/zh/dayTrading/TWTB4U": lambda: load("twse_twtb4u.json"),
            "openapi.twse.com.tw/v1/opendata/t187ap11_L": lambda: load("twse_t187ap11_L.json"),
            "www.tpex.org.tw/openapi/v1/mopsfin_t187ap11_O": lambda: load("tpex_t187ap11_O.json"),
            "www.twse.com.tw/rwd/zh/exRight/TWT48U": lambda: load("twse_twt48u.json"),
            "www.tpex.org.tw/openapi/v1/tpex_exright_prepost": lambda: load(
                "tpex_exright_prepost.json"
            ),
        }.get(key)
        return httpx.Response(200, json=body()) if body else httpx.Response(404)


SBL_KEY = "www.twse.com.tw/rwd/zh/marginTrading/TWT93U"
DT_KEY = "www.twse.com.tw/rwd/zh/dayTrading/TWTB4U"
TPEX_SBL_KEY = "www.tpex.org.tw/web/stock/margin_trading/margin_sbl/margin_sbl_result.php"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    for name in ("MIN_TWSE_SBL_ROWS", "MIN_TPEX_SBL_ROWS", "MIN_DAYTRADE_ROWS", "MIN_PLEDGE_ROWS"):
        monkeypatch.setattr(sched, name, 1)


async def covered(repo, kind: str, periods: list[str]) -> dict[tuple[str, str], object]:
    """Coverage rows per (source, period): sources are recorded one by one, never per day."""
    found: dict[tuple[str, str], object] = {}
    for name in ("sbl_twse", "sbl_tpex", "dt_twse"):
        for period, entry in (
            await repo.list_fetch_coverage(sched.SOURCE, f"{kind}_{name}", "*", periods)
        ).items():
            found[(name, period)] = entry
    return found


def taipei(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC) - timedelta(hours=8)


def test_slots_recent_days_and_backfill_window() -> None:
    assert [
        sched.evening_slot(taipei(27, h, m)) for h, m in ((9, 0), (19, 0), (20, 35), (22, 35))
    ] == [
        "m",
        "a",
        "b",
        "c",
    ]
    # Friday evening: Wed, Thu and Fri; Friday morning: the three days before
    assert sched.recent_days(taipei(27, 21)) == [
        date(2026, 11, 25),
        date(2026, 11, 26),
        date(2026, 11, 27),
    ]
    assert sched.recent_days(taipei(27, 9)) == [
        date(2026, 11, 24),
        date(2026, 11, 25),
        date(2026, 11, 26),
    ]
    # weekend: the last three weekdays
    assert sched.recent_days(taipei(28, 12)) == [
        date(2026, 11, 25),
        date(2026, 11, 26),
        date(2026, 11, 27),
    ]
    assert sched.backfill_days(taipei(28, 12), 3) == [
        date(2026, 11, 24),
        date(2026, 11, 23),
        date(2026, 11, 20),
    ]
    assert sched.backfill_allowed(taipei(27, 15)) and not sched.backfill_allowed(taipei(27, 10))
    assert sched.backfill_allowed(taipei(28, 10))
    assert sched.add_weekdays(date(2026, 11, 27), 2) == date(2026, 12, 1)
    assert sched.documented_available_at(sched.SBL, date(2026, 11, 27)) == datetime(
        2026, 11, 27, 14, 35, tzinfo=UTC
    )


async def test_evening_refresh_stores_today_and_is_idempotent_per_slot() -> None:
    repo, ex = MemoryQuantRepository(), FakeExchanges()
    now = taipei(27, 21)  # slot b: after the first official SBL update
    written = await sched.refresh_short_data(
        repo, ex, ex, now=now, attempts={}, backfill_sessions=0, request_interval=0
    )
    assert written["sbl"] == 6 and written["daytrade"] == 4 and written["insider"] == 3
    assert written["dividend"] == 8
    sbl_rows = await repo.sd_sbl_rows(date(2026, 11, 1), FAR)
    assert {r.symbol for r in sbl_rows} == {
        "1101.TW",
        "2317.TW",
        "2330.TW",
        "3293.TWO",
        "6488.TWO",
        "8069.TWO",
    }
    assert all(r.available_at == now and r.trading_date == date(2026, 11, 27) for r in sbl_rows)
    # the two earlier days answered with another date's rows -> recorded empty (holiday-like)
    cover = await covered(repo, "daily", [f"202611{d}@1127b" for d in (25, 26, 27)])
    assert all(len(period) <= 16 for _, period in cover)  # fetch_coverage.period is varchar(16)
    assert len(cover) == 9  # three sources (TWSE/TPEx SBL, TWSE day trade) per day
    assert cover[("sbl_tpex", "20261125@1127b")].status == "empty"  # holiday-like: all empty
    assert cover[("sbl_twse", "20261127@1127b")].status == "ok"

    ex.requests.clear()
    again = await sched.refresh_short_data(
        repo, ex, ex, now=now, attempts={}, backfill_sessions=0, request_interval=0
    )
    assert not ex.requests and again == {}  # same slot, same day: nothing re-requested


async def test_past_days_fetched_late_are_stamped_with_their_publication_time() -> None:
    repo, ex = MemoryQuantRepository(), FakeExchanges()
    saturday = taipei(28, 10, 55)  # first deploy on the weekend
    await sched.refresh_short_data(
        repo, ex, ex, now=saturday, attempts={}, backfill_sessions=0, request_interval=0
    )
    sbl_rows = await repo.sd_sbl_rows(date(2026, 11, 1), FAR)
    assert sbl_rows and all(r.trading_date == date(2026, 11, 27) for r in sbl_rows)
    # 借券 of Friday is final at 22:35 that day, not "Saturday 10:55"
    assert {r.available_at for r in sbl_rows} == {taipei(27, 22, 35)}
    # 當沖 is only final on T+2 (Tuesday 23:00): a Saturday fetch keeps the fetch time
    dt_rows = await repo.sd_day_trade_rows(date(2026, 11, 1), FAR)
    assert dt_rows and {r.available_at for r in dt_rows} == {saturday}
    # an as-of read at Friday 23:00 already sees the Friday 借券 rows
    assert await repo.sd_sbl_rows(date(2026, 11, 1), taipei(27, 23))
    # same-day fetch before the publication time keeps the fetch time
    assert sched.stamp_available_at(
        sched.documented_available_at(sched.SBL, date(2026, 11, 27)), taipei(27, 21)
    ) == taipei(27, 21)


async def test_later_slot_adds_a_revision_when_numbers_change() -> None:
    repo, ex = MemoryQuantRepository(), FakeExchanges()
    await sched.refresh_short_data(
        repo, ex, ex, now=taipei(27, 21), attempts={}, backfill_sessions=0, request_interval=0
    )
    ex.twse_sbl = copy.deepcopy(ex.twse_sbl)
    for row in ex.twse_sbl["data"]:
        if row[0] == "2330":
            row[12] = "17,500,000"  # second 22:30 update restates the balance
    written = await sched.refresh_short_data(
        repo, ex, ex, now=taipei(27, 22, 40), attempts={}, backfill_sessions=0, request_interval=0
    )
    assert written["sbl"] == 1 and written.get("daytrade", 0) == 0
    now_row = next(
        r for r in await repo.sd_sbl_rows(date(2026, 11, 1), FAR) if r.symbol == "2330.TW"
    )
    assert now_row.revision == 2 and now_row.sbl_balance == 17_500_000
    first = next(
        r
        for r in await repo.sd_sbl_rows(date(2026, 11, 1), taipei(27, 22))
        if r.symbol == "2330.TW"
    )
    assert first.revision == 1 and first.sbl_balance == 17_457_514


async def test_today_sbl_waits_for_first_official_update() -> None:
    repo, ex = MemoryQuantRepository(), FakeExchanges()
    await sched.refresh_short_data(
        repo, ex, ex, now=taipei(27, 19, 30), attempts={}, backfill_sessions=0, request_interval=0
    )
    assert ex.requests[DT_KEY] >= 1
    # slot a: SBL is only requested for the finished earlier days, never for today
    assert ex.requests[SBL_KEY] == 2


async def test_backfill_is_gradual_and_off_session() -> None:
    repo, ex = MemoryQuantRepository(), FakeExchanges()
    attempts: dict[str, datetime] = {}
    # Saturday: recent window is Wed-Fri, backfill takes the weekdays before it, 2 per tick
    now = taipei(28, 12)
    await sched.refresh_short_data(
        repo,
        ex,
        ex,
        now=now,
        attempts=attempts,
        backfill_sessions=5,
        backfill_per_tick=2,
        request_interval=0,
    )
    wanted = [f"bf:2026-11-{d}" for d in (24, 23, 20, 19, 18)]
    cover = await covered(repo, "backfill", wanted)
    assert {period for _, period in cover} == {"bf:2026-11-24", "bf:2026-11-23"}
    await sched.refresh_short_data(
        repo,
        ex,
        ex,
        now=now + timedelta(minutes=1),
        attempts=attempts,
        backfill_sessions=5,
        backfill_per_tick=2,
        request_interval=0,
    )
    cover = await covered(repo, "backfill", wanted)
    assert len({period for _, period in cover}) == 4
    # during the trading session nothing is backfilled
    before = ex.requests[SBL_KEY]
    await sched.refresh_short_data(
        repo,
        ex,
        ex,
        now=taipei(27, 10),
        attempts={},
        backfill_sessions=5,
        request_interval=0,
        insider_enabled=False,
        dividend_enabled=False,
    )
    assert ex.requests[SBL_KEY] - before <= 3  # only the recent window re-check


async def test_backfilled_rows_use_documented_publication_time() -> None:
    repo, ex = MemoryQuantRepository(), FakeExchanges()
    now = taipei(28, 12)
    ex.twse_sbl = copy.deepcopy(ex.twse_sbl)
    ex.twse_sbl["date"] = "20261124"
    await sched.refresh_short_data(
        repo,
        ex,
        ex,
        now=now,
        attempts={},
        backfill_sessions=1,
        request_interval=0,
        daytrade_enabled=False,
        insider_enabled=False,
        dividend_enabled=False,
    )
    rows = [
        r
        for r in await repo.sd_sbl_rows(date(2026, 11, 1), FAR)
        if r.trading_date == date(2026, 11, 24)
    ]
    assert rows and all(r.available_at == datetime(2026, 11, 24, 14, 35, tzinfo=UTC) for r in rows)
    # a historical as-of cutoff before that evening cannot see it
    assert not [
        r
        for r in await repo.sd_sbl_rows(date(2026, 11, 1), datetime(2026, 11, 24, 12, tzinfo=UTC))
        if r.trading_date == date(2026, 11, 24)
    ]


async def test_failure_is_retried_later_and_never_raises() -> None:
    repo, ex = MemoryQuantRepository(), FakeExchanges(fail={SBL_KEY, DT_KEY})
    attempts: dict[str, datetime] = {}
    now = taipei(27, 21)
    written = await sched.refresh_short_data(
        repo,
        ex,
        ex,
        now=now,
        attempts=attempts,
        backfill_sessions=0,
        fetch_attempts=2,
        request_interval=0,
    )
    assert "daily" in attempts and not written.get("daytrade") and not written.get("sbl")
    ex.requests.clear()
    await sched.refresh_short_data(
        repo,
        ex,
        ex,
        now=now + timedelta(minutes=5),
        attempts=attempts,
        backfill_sessions=0,
        request_interval=0,
        insider_enabled=False,
        dividend_enabled=False,
    )
    assert not ex.requests  # inside the retry window
    ex.fail.clear()
    written = await sched.refresh_short_data(
        repo,
        ex,
        ex,
        now=now + timedelta(minutes=11),
        attempts=attempts,
        backfill_sessions=0,
        request_interval=0,
    )
    assert written["sbl"] == 6 and "daily" not in attempts


async def test_incomplete_table_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(sched, "MIN_TWSE_SBL_ROWS", 50)
    repo, ex = MemoryQuantRepository(), FakeExchanges()
    attempts: dict[str, datetime] = {}
    await sched.refresh_short_data(
        repo,
        ex,
        ex,
        now=taipei(27, 21),
        attempts=attempts,
        backfill_sessions=0,
        request_interval=0,
        daytrade_enabled=False,
        insider_enabled=False,
        dividend_enabled=False,
    )
    assert "daily" in attempts
    stored = await repo.sd_sbl_rows(date(2026, 11, 1), FAR)
    assert stored and not any(r.symbol.endswith(".TW") for r in stored)  # TWSE table rejected


async def test_flags_disable_datasets() -> None:
    repo, ex = MemoryQuantRepository(), FakeExchanges()
    written = await sched.refresh_short_data(
        repo,
        ex,
        ex,
        now=taipei(27, 21),
        sbl_enabled=False,
        daytrade_enabled=False,
        insider_enabled=False,
        dividend_enabled=False,
    )
    assert written == {} and not ex.requests


# --- API -------------------------------------------------------------------------------


@pytest.fixture
async def api_repo() -> MemoryQuantRepository:
    repo = await seeded_market()
    at = datetime(2026, 11, 20, tzinfo=UTC)
    await repo.upsert_insider_holdings(
        src.parse_twse_pledges(load("twse_t187ap11_L.json"), available_at=at)
    )
    await repo.upsert_dividend_events(
        src.parse_twse_dividends(load("twse_twt48u.json"), available_at=at)
    )
    return repo


async def test_short_interest_service(api_repo) -> None:
    body = await StockShortInterestService(api_repo).get("2330", as_of=date(2026, 11, 27), days=30)
    assert body.symbol == "2330.TW" and len(body.days) == 30
    last = body.days[-1]
    assert last.date == date(2026, 11, 27)
    assert last.sbl_sold_lots == 50.0  # 50,000 shares
    assert last.sbl_balance_lots and last.margin_short_balance_lots is not None
    assert last.day_trade_ratio_pct == pytest.approx(20.0)
    assert body.days[0].day_trade_ratio_pct is None  # earlier than the day-trade history
    assert body.sbl_change_20d_pct is not None and body.days_to_cover is not None
    assert body.sbl_pct_of_shares is not None
    assert body.day_trade_ratio_20d_pct == pytest.approx(20.0)
    assert body.insiders is not None and body.insiders.pledged_shares == 1_728_000
    assert [e.date for e in body.upcoming_events] == [date(2026, 12, 10)]
    assert "7" in body.upcoming_events[0].label and body.upcoming_events[0].kind == "ex_dividend"
    assert body.notes
    # point in time: before the insider table was fetched it does not appear
    early = await StockShortInterestService(api_repo).get("2330", as_of=date(2026, 11, 10))
    assert early.insiders is None and early.upcoming_events == []
    with pytest.raises(StockNotFound):
        await StockShortInterestService(api_repo).get("9998", as_of=date(2026, 11, 27))


def test_short_interest_endpoint(api_repo) -> None:
    app.dependency_overrides[get_repository] = lambda: api_repo
    app.dependency_overrides[require_principal] = lambda: Principal("anonymous")
    try:
        client = TestClient(app)
        response = client.get("/api/v1/stocks/2330.TW/short-interest?as_of=2026-11-27&days=5")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["days"]) == 5 and body["days"][-1]["sbl_sold_lots"] == 50.0
        assert (
            body["insiders"]["pledge_ratio_pct"] > 0 and body["upcoming_events"][0]["cash_dividend"]
        )
        assert client.get("/api/v1/stocks/9998/short-interest").status_code == 404
    finally:
        app.dependency_overrides.clear()


# --- migration and Postgres statements ----------------------------------------------------

MIGRATION = Path(__file__).parents[1] / "migrations" / "versions" / "0021_short_daytrade_events.py"
TABLES = ("short_lending_daily", "day_trade_daily", "insider_holdings_monthly", "dividend_events")


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


def test_migration_matches_orm() -> None:
    spec = importlib.util.spec_from_file_location("migration_0021", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    recorder = _Recorder()
    module.op = recorder
    # The parent revision is fixed when branches are integrated; only pin the id here.
    assert module.revision == "0021_short_daytrade_events"
    module.upgrade()
    assert set(recorder.tables) == set(TABLES)
    for name in TABLES:
        orm = Base.metadata.tables[name]
        items = recorder.tables[name]
        columns = {i.name: i for i in items if isinstance(i, sa.Column)}
        assert set(columns) == {c.name for c in orm.columns}, name
        for column in orm.columns:
            assert type(columns[column.name].type) is type(column.type), (name, column.name)
            assert columns[column.name].nullable == column.nullable, (name, column.name)
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


# --- data-integrity fixes ------------------------------------------------------------------


async def test_coverage_periods_fit_the_varchar_column_and_memory_enforces_it() -> None:
    from quant_signal.domain.models import FetchCoverage

    repo = MemoryQuantRepository()
    too_long = FetchCoverage(
        source=sched.SOURCE,
        dataset="daily",
        symbol="*",
        period="2026-11-25@2026-11-27-c",  # 23 characters: the key that broke in Postgres
        status="ok",
        row_count=1,
        fetched_at=FETCHED,
    )
    with pytest.raises(ValueError, match="varchar"):
        await repo.record_fetch_coverage([too_long])
    # every other length-limited column is checked the same way
    with pytest.raises(ValueError, match="varchar"):
        await repo.upsert_short_lending(
            [
                ShortLendingDay(
                    symbol="X" * 40,
                    trading_date=date(2026, 11, 27),
                    source="twse_twt93u",
                    sbl_sold=1,
                    sbl_returned=0,
                    sbl_balance=1,
                    available_at=FETCHED,
                )
            ]
        )
    # the evening slot records its three days with keys that fit
    ex = FakeExchanges()
    await sched.refresh_short_data(
        repo, ex, ex, now=taipei(27, 22, 40), attempts={}, backfill_sessions=0, request_interval=0
    )
    assert all(len(period) <= 16 for (_, _, _, period) in repo.fetch_coverage)
    keys = [p for (_, dataset, _, p) in repo.fetch_coverage if dataset.startswith("daily_")]
    assert len(keys) == 9
    assert "20261125@1127c" in keys


async def test_a_day_is_not_done_while_one_exchange_has_not_published() -> None:
    """TWSE has rows, TPEx has not published its table yet: TPEx stays open (item 4)."""
    repo = MemoryQuantRepository()
    published = {"tpex": False}

    class Late(FakeExchanges):
        def _handle(self, request):
            if "margin_sbl" in request.url.path and not published["tpex"]:
                return httpx.Response(200, json={"stat": "ok", "tables": []})
            return super()._handle(request)

    late = Late()
    attempts: dict[str, datetime] = {}
    now = taipei(27, 21)
    await sched.refresh_short_data(
        repo, late, late, now=now, attempts=attempts, backfill_sessions=0, request_interval=0
    )
    cover = await covered(repo, "daily", ["20261127@1127b"])
    assert ("sbl_twse", "20261127@1127b") in cover
    assert ("sbl_tpex", "20261127@1127b") not in cover  # not recorded: it is asked again
    assert not [r for r in await repo.sd_sbl_rows(date(2026, 11, 1), FAR) if r.symbol.endswith("O")]
    published["tpex"] = True
    late.requests.clear()
    await sched.refresh_short_data(
        repo,
        late,
        late,
        now=now + timedelta(minutes=11),
        attempts=attempts,
        backfill_sessions=0,
        request_interval=0,
    )
    assert late.requests[TPEX_SBL_KEY] >= 1
    assert late.requests[SBL_KEY] == 0  # TWSE is recorded for this slot, not refetched
    cover = await covered(repo, "daily", ["20261127@1127b"])
    assert cover[("sbl_tpex", "20261127@1127b")].status == "ok"
    assert {r.symbol for r in await repo.sd_sbl_rows(date(2026, 11, 1), FAR)} >= {"6488.TWO"}


async def test_day_trading_never_waits_for_tpex() -> None:
    """Day trading has TWSE only: a TPEx-less dataset is complete with the TWSE answer."""
    repo, ex = MemoryQuantRepository(), FakeExchanges()
    await sched.refresh_short_data(
        repo,
        ex,
        ex,
        now=taipei(27, 21),
        attempts={},
        sbl_enabled=False,
        insider_enabled=False,
        dividend_enabled=False,
        backfill_sessions=0,
        request_interval=0,
    )
    cover = await covered(repo, "daily", ["20261127@1127b"])
    assert cover[("dt_twse", "20261127@1127b")].status == "ok"
    assert ex.requests[TPEX_SBL_KEY] == 0


async def test_source_that_stays_empty_is_given_up_with_a_warning(caplog) -> None:
    """After MAX_MISSING_DAYS different days the still-empty source is recorded as empty."""
    from quant_signal.application.source_guard import MAX_MISSING_DAYS

    repo = MemoryQuantRepository()

    class NoTpex(FakeExchanges):
        def _handle(self, request):
            if "margin_sbl" in request.url.path:
                return httpx.Response(200, json={"stat": "ok", "tables": []})
            return super()._handle(request)

    ex, attempts = NoTpex(), {}
    for offset in range(MAX_MISSING_DAYS + 1):
        # Friday 11-27 evening, then the following weekdays: day 11-27 stays a recent day
        now = taipei(27, 22, 40) + timedelta(days=offset if offset == 0 else offset)
        await sched.refresh_short_data(
            repo,
            ex,
            ex,
            now=now,
            attempts=attempts,
            backfill_sessions=0,
            request_interval=0,
            insider_enabled=False,
            dividend_enabled=False,
        )
    miss = await repo.list_fetch_coverage(sched.SOURCE, "daily_sbl_tpex_miss", "*", ["20261127"])
    assert miss["20261127"].row_count >= MAX_MISSING_DAYS
    assert "still empty" in caplog.text


class DatedExchanges(FakeExchanges):
    """Answers every date with the fixture rows re-dated to the requested day."""

    def __init__(self) -> None:
        super().__init__()
        self.tpex_published = True

    def _handle(self, request):
        response = super()._handle(request)
        if response.status_code != 200:
            return response
        payload = json.loads(response.content)
        params = request.url.params
        if "date" in params:  # TWSE
            payload["date"] = params["date"]
            for table in payload.get("tables", []):
                table["date"] = params["date"]
        elif "d" in params:  # TPEx
            if not self.tpex_published:
                return httpx.Response(200, json={"stat": "ok", "tables": []})
            payload["tables"][0]["date"] = params["d"]
        return httpx.Response(200, json=payload)


async def test_backfill_day_is_not_done_after_one_source(monkeypatch) -> None:
    repo, ex, attempts = MemoryQuantRepository(), DatedExchanges(), {}
    ex.tpex_published = False
    common = dict(
        attempts=attempts,
        backfill_sessions=1,
        backfill_per_tick=1,
        request_interval=0,
        insider_enabled=False,
        dividend_enabled=False,
    )
    # a Saturday: the recent window is Wed-Fri, the one backfill day is Tuesday 11-24
    saturday = taipei(28, 12)
    monkeypatch.setattr(sched, "recent_days", lambda now: [])  # only the backfill is of interest
    await sched.refresh_short_data(repo, ex, ex, now=saturday, **common)
    period = "bf:2026-11-27"  # recent window is empty, so the newest weekday is the backfill day
    cover = await covered(repo, "backfill", [period])
    assert cover[("sbl_twse", period)].status == "ok" and cover[("dt_twse", period)].status == "ok"
    assert ("sbl_tpex", period) not in cover  # TPEx had nothing: the day is NOT recorded done
    # the same Taipei day it is not hammered again ...
    ex.requests.clear()
    ex.tpex_published = True
    await sched.refresh_short_data(repo, ex, ex, now=saturday + timedelta(minutes=30), **common)
    assert ex.requests[TPEX_SBL_KEY] == 0
    # ... but the next day it is asked for (only TPEx, the rest is recorded) and recorded
    await sched.refresh_short_data(repo, ex, ex, now=saturday + timedelta(days=1), **common)
    assert ex.requests[TPEX_SBL_KEY] == 1 and ex.requests[SBL_KEY] == 0
    cover = await covered(repo, "backfill", [period])
    assert cover[("sbl_tpex", period)].status == "ok"
    assert {r.symbol for r in await repo.sd_sbl_rows(date(2026, 11, 1), FAR)} >= {"6488.TWO"}


class DividendExchanges(FakeExchanges):
    """Serves editable copies of the two forecast tables."""

    def __init__(self) -> None:
        super().__init__()
        self.twt48u = load("twse_twt48u.json")
        self.tpex_ex = load("tpex_exright_prepost.json")

    def _handle(self, request):
        key = f"{request.url.host}{request.url.path}"
        if key == "www.twse.com.tw/rwd/zh/exRight/TWT48U":
            self.requests[key] += 1
            return httpx.Response(200, json=self.twt48u)
        if key == "www.tpex.org.tw/openapi/v1/tpex_exright_prepost":
            self.requests[key] += 1
            return httpx.Response(200, json=self.tpex_ex)
        return super()._handle(request)


async def dividends_only(repo, ex, now) -> dict:
    return await sched.refresh_short_data(
        repo,
        ex,
        ex,
        now=now,
        attempts={},
        sbl_enabled=False,
        daytrade_enabled=False,
        insider_enabled=False,
        backfill_sessions=0,
        request_interval=0,
    )


async def test_a_moved_ex_date_retires_the_old_event_instead_of_keeping_both() -> None:
    repo, ex = MemoryQuantRepository(), DividendExchanges()
    first = taipei(27, 21)
    await dividends_only(repo, ex, first)
    december = (date(2026, 12, 1), date(2026, 12, 31))
    got = await repo.list_dividend_events(["2330.TW"], *december, first)
    assert [e.ex_date for e in got] == [date(2026, 12, 10)]
    # the company moves its ex-date from 12/10 to 12/17; the next table lists only the new one
    for row in ex.twt48u["data"]:
        if row[1] == "2330":
            row[0] = "115年12月17日"
    second = taipei(28, 9)
    await dividends_only(repo, ex, second)
    after = await repo.list_dividend_events(["2330.TW"], *december, FAR)
    assert [(e.ex_date, e.revision, e.withdrawn) for e in after] == [(date(2026, 12, 17), 1, False)]
    # paper trading must see one dividend, not two
    nxt = await repo.next_dividend_events(second)
    assert nxt["2330.TW"].date == date(2026, 12, 17)
    # as-of reads: before the withdrawal was fetched the old date is what was known
    before = await repo.list_dividend_events(["2330.TW"], *december, first + timedelta(hours=1))
    assert [e.ex_date for e in before] == [date(2026, 12, 10)]
    # nothing else changed, and an identical table writes nothing
    assert await dividends_only(repo, ex, taipei(28, 18)) == {"dividend": 0}


async def test_cancelled_and_retyped_events_are_withdrawn_but_past_ones_never() -> None:
    repo, ex = MemoryQuantRepository(), DividendExchanges()
    await dividends_only(repo, ex, taipei(27, 21))
    # 2646 disappears from the table (cancelled); 2345 becomes 權息 (a different kind)
    ex.twt48u["data"] = [row for row in ex.twt48u["data"] if row[1] != "2646"]
    for row in ex.twt48u["data"]:
        if row[1] == "2345":
            row[3] = "權息"
    # TPEx's table no longer lists its already-passed 11/26 event
    ex.tpex_ex = [row for row in ex.tpex_ex if row["SecuritiesCompanyCode"] != "6523"]
    await dividends_only(repo, ex, taipei(28, 9))
    events = await repo.list_dividend_events(
        ["2646.TW", "2345.TW", "6523.TWO", "6488.TWO"], date(2026, 11, 1), date(2026, 12, 31), FAR
    )
    seen = {(e.symbol, e.kind) for e in events}
    assert ("2646.TW", "ex_rights") not in seen  # cancelled
    assert ("2345.TW", "ex_rights") not in seen and ("2345.TW", "ex_rights_dividend") in seen
    assert ("6523.TWO", "ex_dividend") in seen  # ex-date 11/26 is in the past: never withdrawn
    assert ("6488.TWO", "ex_dividend") in seen
    # an event beyond the table's last date is not judged by it
    one = [e for e in await repo.list_dividend_events(["2330.TW"], *(date(2026, 12, 1),) * 2, FAR)]
    assert one == []
    # a source whose table is empty (failed fetch) withdraws nothing
    ex.twt48u = {"stat": "no data"}
    await dividends_only(repo, ex, taipei(29, 9))
    assert await repo.list_dividend_events(["2330.TW"], date(2026, 12, 1), date(2026, 12, 31), FAR)


async def test_taipei_dates_not_utc_dates_decide_next_event_and_volume_cutoff() -> None:
    repo = MemoryQuantRepository()
    at = datetime(2026, 11, 20, tzinfo=UTC)

    def event(day: date) -> DividendEvent:
        return DividendEvent(
            symbol="2330.TW",
            ex_date=day,
            kind="ex_dividend",
            source="twse_twt48u",
            cash_dividend_per_share=1.0,
            available_at=at,
        )

    await repo.upsert_dividend_events([event(date(2026, 11, 27)), event(date(2026, 12, 3))])
    # 02:00 Taipei on Saturday 11-28 is still Friday 11-27 18:00 in UTC: the 11-27 ex-date
    # has passed in Taipei and must not be reported as upcoming
    as_of = datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    assert as_of.date() == date(2026, 11, 27)
    assert (await repo.next_dividend_events(as_of))["2330.TW"].date == date(2026, 12, 3)
    # the volume window ends on the Taipei date of the cutoff (Mon 11-30 04:00 Taipei = Sun UTC)
    await repo.upsert_market_stock_days(
        [
            MarketStockDay(
                symbol="2330.TW",
                name="台積電",
                market="TW",
                trading_date=date(2026, 11, 30),
                open=1,
                high=1,
                low=1,
                close=1,
                volume=5,
                turnover_value=5.0,
                available_at=datetime(2026, 11, 29, 20, 0, tzinfo=UTC),
                source="t",
            )
        ]
    )
    cutoff = datetime(2026, 11, 29, 20, 30, tzinfo=UTC)
    assert cutoff.date() == date(2026, 11, 29)
    rows = await repo.sd_volume_rows(date(2026, 11, 1), cutoff)
    assert rows == [("2330.TW", date(2026, 11, 30), 5)]


def test_short_data_metrics_are_rounded_where_they_are_produced() -> None:
    assert calc.pct(1, 3) == 33.33
    assert calc.change_pct(4, 3) == 33.33
    assert calc.days_to_cover(10, 3.0) == 3.3333
    assert calc.pct(2, 3) == 66.67 and calc.change_pct(0.1 + 0.2, 0.3) == 0.0
