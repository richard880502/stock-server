from __future__ import annotations

import asyncio
import importlib.util
import os
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.adapters.rest.security import Principal, require_principal
from quant_signal.application import derivatives_data as sched
from quant_signal.application.derivatives_observations import refresh_observation_futures
from quant_signal.application.derivatives_queries import (
    MarketDerivativesService,
    StockFuturesService,
)
from quant_signal.application.stocks import StockNotFound
from quant_signal.domain.derivatives import DerivativesRows, StockFuturesDay
from quant_signal.domain.models import MarketObservation, MarketStockDay
from quant_signal.infrastructure.db.models import Base
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.infrastructure.providers import taifex as src
from quant_signal.quant import derivatives as calc
from quant_signal.quant.market_environment import MarketEnvironmentEngine

FIXTURES = Path(__file__).parent / "fixtures" / "derivatives"
FAR = datetime(2027, 1, 1, tzinfo=UTC)


def text(name: str) -> str:
    return src.decode((FIXTURES / name).read_bytes())


def specs() -> dict[str, src.ContractSpec]:
    listed = src.parse_stock_lists((FIXTURES / "stock_lists.html").read_text(encoding="utf-8"))
    adjusted = src.parse_ssf_adjusted((FIXTURES / "ssf_adjusted.json").read_text(encoding="utf-8"))
    return {spec.code: spec for spec in [*listed, *adjusted]}


def taipei(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC) - timedelta(hours=8)


# --- parsers ---------------------------------------------------------------------------


def test_futures_institutional_parser_keeps_index_futures_only() -> None:
    rows = src.parse_futures_institutional(text("fut_institutional.csv"))
    assert {r.contract for r in rows} == {"TX", "MTX", "TMF"}  # 電子期貨 is dropped
    assert len(rows) == 18  # 2 dates x 3 contracts x 3 investors
    foreign = next(
        r
        for r in rows
        if (r.trading_date, r.contract, r.investor) == (date(2026, 11, 27), "TX", "foreign")
    )
    assert (foreign.long_oi_contracts, foreign.short_oi_contracts) == (7862, 115365)
    assert foreign.net_oi_contracts == -107503
    assert foreign.long_oi_value_ntd_k == 61_909_941 and foreign.long_trade_contracts == 53803
    assert foreign.short_trade_contracts == 53882 and foreign.source == "taifex"
    # documented publication time: 18:00 Taipei on the trading day
    assert foreign.available_at == datetime(2026, 11, 27, 10, 0, tzinfo=UTC)


def test_cp950_bytes_decode_and_html_error_pages_are_rejected() -> None:
    raw = (FIXTURES / "fut_institutional.csv").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    assert "臺股期貨" in src.decode(raw)
    with pytest.raises(ValueError):
        src.parse_futures_institutional("<html><body>404</body></html>")
    with pytest.raises(ValueError):
        src.parse_pc_ratio("")
    with pytest.raises(ValueError):
        src.parse_large_traders(text("fut_institutional.csv"))


def test_odd_futures_rows_are_skipped() -> None:
    header = text("fut_institutional.csv").splitlines()[0]
    body = [
        "2026/11/27,臺股期貨,外資及陸資,1,2,3,4,0,0,5,6,7,8,-2,-2",  # fine
        "2026/11/27,臺股期貨,投信,1,2,3,4,0,0,-,6,7,8,-2,-2",  # '-' cell
        "2026/11/27,臺股期貨,不明身份,1,2,3,4,0,0,5,6,7,8,-2,-2",  # unknown investor
        "合計,臺股期貨,外資及陸資,1,2,3,4,0,0,5,6,7,8,-2,-2",  # bad date
        "",
    ]
    rows = src.parse_futures_institutional("\r\n".join([header, *body]))
    assert [(r.investor, r.long_oi_contracts) for r in rows] == [("foreign", 5)]
    thousands = '2026/11/27,臺股期貨,自營商,"1,000",2,3,4,0,0,"5,000",6,7,8,-2,-2'
    assert (
        src.parse_futures_institutional("\r\n".join([header, thousands]))[0].long_oi_contracts
        == 5000
    )


def test_pc_ratio_parser_handles_trailing_comma() -> None:
    rows = src.parse_pc_ratio(text("pc_ratio.csv"))
    assert [r.trading_date for r in rows] == [
        date(2026, 11, 27),
        date(2026, 11, 26),
        date(2026, 11, 25),
    ]
    first = rows[0]
    assert (first.put_volume_contracts, first.call_volume_contracts) == (237611, 289506)
    assert first.volume_ratio_pct == 82.07 and first.oi_ratio_pct == 46.67
    assert (first.put_oi_contracts, first.call_oi_contracts) == (39714, 85100)


def test_options_institutional_parser_splits_calls_and_puts() -> None:
    rows = src.parse_options_institutional(text("calls_puts.csv"))
    assert len(rows) == 12 and {r.contract for r in rows} == {"TXO"}
    pick = {(r.trading_date, r.call_put, r.investor): r for r in rows}
    call = pick[(date(2026, 11, 27), "CALL", "foreign")]
    assert (call.buy_oi_contracts, call.sell_oi_contracts, call.net_oi_contracts) == (
        32668,
        19652,
        13016,
    )
    assert pick[(date(2026, 11, 27), "PUT", "foreign")].net_oi_contracts == 3661


def test_large_traders_parser_uses_all_months_rows_only() -> None:
    rows = src.parse_large_traders(text("large_traders.csv"))
    assert {r.contract for r in rows} == {"TX"}  # 電子期貨 TE is not requested
    assert len(rows) == 4  # 2 dates x (all, specific); monthly and weekly rows dropped
    day = {(r.trading_date, r.trader_type): r for r in rows}
    all_traders = day[(date(2026, 11, 27), "all")]
    assert (all_traders.top5_long_contracts, all_traders.top5_short_contracts) == (98919, 65732)
    assert (all_traders.top10_long_contracts, all_traders.top10_short_contracts) == (110344, 97154)
    assert all_traders.market_oi_contracts == 140027
    assert day[(date(2026, 11, 27), "specific")].top10_long_contracts == 108088


def test_stock_underlying_lists_cover_small_contracts_etfs_and_otc() -> None:
    table = specs()
    assert table["CDF"].symbol == "2330.TW" and table["CDF"].shares_per_contract == 2000
    assert table["QFF"].symbol == "2330.TW" and table["QFF"].shares_per_contract == 100  # 小型
    assert table["NYF"].symbol == "0050.TW" and table["NYF"].shares_per_contract == 10_000  # ETF
    assert table["SRF"].shares_per_contract == 1_000  # 小型 ETF
    assert table["OWF"].symbol == "6488.TWO" and table["PBF"].shares_per_contract == 100  # OTC
    assert table["CM1"].adjusted and table["CM1"].shares_per_contract == 2020  # 調整型


def test_daily_futures_report_skips_spreads_and_merges_sessions() -> None:
    codes = set(specs())
    months = src.parse_futures_daily(text("stock_futures.csv"), codes)
    assert all(re_month.month.isdigit() and len(re_month.month) == 6 for re_month in months)
    assert not any("/" in m.month for m in months)
    tsmc = next(m for m in months if (m.code, m.month) == ("CDF", "202612"))
    assert tsmc.open_interest == 24521 and tsmc.settlement == 2159
    # 一般 + 盤後 volume of the standard TSMC contract
    qff = next(m for m in months if (m.code, m.month) == ("QFF", "202612"))
    assert qff.volume == 44818 + 38650 and qff.open_interest == 73330
    # the after-hours row has no settlement/OI, only the regular one does
    far = next(m for m in months if (m.code, m.month) == ("SRF", "202706"))
    assert far.volume == 0 and far.settlement == 93.3 and far.open_interest == 68


def test_stock_futures_aggregate_standard_equivalents_and_near_month() -> None:
    table = specs()
    rows = src.build_stock_futures_days(
        src.parse_futures_daily(text("stock_futures.csv"), set(table)), table
    )
    by_symbol = {r.symbol: r for r in rows}
    assert set(by_symbol) == {"0050.TW", "00878.TW", "2330.TW", "2887.TW"}
    tsmc = by_symbol["2330.TW"]
    months = {
        m.month: m
        for m in src.parse_futures_daily(text("stock_futures.csv"), {"CDF", "QFF"})
        if m.code == "CDF"
    }
    qff_shares = sum(
        (m.open_interest or 0) * 100
        for m in src.parse_futures_daily(text("stock_futures.csv"), {"QFF"})
    )
    cdf_shares = sum((m.open_interest or 0) * 2000 for m in months.values())
    assert tsmc.open_interest_shares == qff_shares + cdf_shares
    assert tsmc.total_open_interest == pytest.approx((qff_shares + cdf_shares) / 2000)
    assert tsmc.near_month == "202612" and tsmc.near_settlement == 2159  # the standard contract
    assert tsmc.near_open_interest == pytest.approx((24521 * 2000 + 73330 * 100) / 2000)
    assert tsmc.product_count == 2
    etf = by_symbol["0050.TW"]  # NYF 10,000 units + SRF 1,000 units
    assert etf.near_settlement == 92.9
    assert etf.near_open_interest == pytest.approx((25444 * 10_000 + 20641 * 1_000) / 10_000)
    # 調整型 CM1 (2,020 shares) joins 2887 as 100 contracts of 2,020 shares
    cathay = by_symbol["2887.TW"]
    assert cathay.open_interest_shares >= 100 * 2020 + 1818 * 2000


def test_delivery_month_rules() -> None:
    assert src.third_wednesday(2026, 12) == date(2026, 12, 16)
    assert src.third_wednesday(2026, 11) == date(2026, 11, 18)
    table = {"CDF": specs()["CDF"]}
    on_settlement = [
        src.ContractMonth(date(2026, 11, 18), "CDF", "202611", 5, 2100.0, 10),
        src.ContractMonth(date(2026, 11, 18), "CDF", "202612", 7, 2110.0, 20),
    ]
    day = src.build_stock_futures_days(on_settlement, table)[0]
    assert day.near_month == "202612"  # the contract settling that day is no longer "near"
    no_price = [src.ContractMonth(date(2026, 11, 26), "CDF", "202612", 0, None, None)]
    empty = src.build_stock_futures_days(no_price, table)[0]
    assert empty.near_month is None and empty.near_settlement is None
    assert empty.total_open_interest == 0


def test_options_oi_levels_pick_nearest_monthly_and_weekly() -> None:
    rows = src.parse_options_daily(text("options_oi.csv"))
    levels = {item.kind: item for item in src.options_oi_levels(rows)}
    # 202611F4 expires on the trading date itself and is skipped
    assert levels["weekly"].expiry == "202612W1" and levels["weekly"].expiry_date == date(
        2026, 12, 2
    )
    assert (levels["weekly"].max_call_strike, levels["weekly"].max_call_oi_contracts) == (
        38800.0,
        3611,
    )
    assert (levels["weekly"].max_put_strike, levels["weekly"].max_put_oi_contracts) == (
        32400.0,
        1575,
    )
    assert levels["monthly"].expiry == "202612"
    assert (levels["monthly"].max_call_strike, levels["monthly"].max_put_strike) == (
        50000.0,
        49800.0,
    )
    assert src.options_oi_levels([]) == []


def test_vix_parser_reads_close_and_last_minute_average() -> None:
    rows = src.parse_vix(text("vix_202611.txt"))
    last = rows[-1]
    assert last.trading_date == date(2026, 11, 27)
    assert (last.vix_close, last.vix_last_minute_avg) == (51.94, 51.93)
    with pytest.raises(ValueError):
        src.parse_vix("<html></html>")


def test_request_forms() -> None:
    assert src.fut_institutional_form(date(2026, 11, 1), date(2026, 11, 27)) == {
        "queryStartDate": "2026/11/01",
        "queryEndDate": "2026/11/27",
        "commodityId": "",
    }
    assert src.fut_daily_form(date(2026, 11, 1), date(2026, 11, 27))["commodity_id"] == "all"
    assert src.opt_daily_form(date(2026, 11, 27))["queryStartDate"] == "2026/11/27"
    assert src.vix_url(date(2026, 11, 27)).endswith("/202611new.txt")


# --- derived measures -------------------------------------------------------------------


@pytest.fixture
def fixture_rows() -> DerivativesRows:
    return DerivativesRows(
        futures=src.parse_futures_institutional(text("fut_institutional.csv")),
        pc_ratio=src.parse_pc_ratio(text("pc_ratio.csv")),
        options=src.parse_options_institutional(text("calls_puts.csv")),
        large_traders=src.parse_large_traders(text("large_traders.csv")),
        vix=src.parse_vix(text("vix_202611.txt")),
        oi_levels=src.options_oi_levels(src.parse_options_daily(text("options_oi.csv"))),
    )


def test_tx_equivalent_net_oi_uses_contract_multipliers(fixture_rows) -> None:
    equiv = calc.tx_equivalent_net_oi(fixture_rows.futures, "foreign")
    # TX -107,503 + MTX 3,699 / 4 + TMF -2,419 / 20
    assert equiv[date(2026, 11, 27)] == pytest.approx(-107503 + 3699 / 4 - 2419 / 20)
    assert equiv[date(2026, 11, 26)] == pytest.approx(-106275 - 647 / 4 - 1332 / 20)
    change = calc.day_over_day(equiv)
    assert change[date(2026, 11, 26)] is None
    assert change[date(2026, 11, 27)] == pytest.approx(-106699.2 - -106503.35)
    assert calc.tx_equivalent_net_oi([], "foreign") == {}
    only_tx = [r for r in fixture_rows.futures if r.contract == "TX"]
    assert calc.tx_equivalent_net_oi(only_tx, "foreign")[date(2026, 11, 27)] == -107503
    only_mtx = [r for r in fixture_rows.futures if r.contract == "MTX"]
    assert calc.tx_equivalent_net_oi(only_mtx, "foreign") == {}  # TX is required


def test_market_days_combine_every_dataset(fixture_rows) -> None:
    days = calc.build_market_days(fixture_rows, days=60)
    assert [d.date for d in days][-2:] == [date(2026, 11, 26), date(2026, 11, 27)]
    last = days[-1]
    assert last.foreign_tx_net_oi == -107503 and last.trust_tx_net_oi == 104355
    assert last.dealer_tx_net_oi == -3566
    assert last.foreign_tx_equiv_net_oi == pytest.approx(-106699.2)
    assert last.foreign_net_oi_change == pytest.approx(-195.85)
    assert last.pc_volume_ratio == 82.07 and last.pc_oi_ratio == 46.67
    assert (last.foreign_call_net_oi, last.foreign_put_net_oi) == (13016, 3661)
    assert (last.top5_net_oi, last.top10_net_oi) == (33187, 13190)
    assert last.vix == 51.94
    assert calc.build_market_days(fixture_rows, days=1)[0].date == date(2026, 11, 27)
    # a date covered by one dataset only leaves the others None, never 0
    partial = calc.build_market_days(DerivativesRows(pc_ratio=fixture_rows.pc_ratio[:1]), days=5)
    assert partial[0].foreign_tx_equiv_net_oi is None and partial[0].vix is None
    assert partial[0].pc_volume_ratio == 82.07
    latest = calc.latest_market_summary(fixture_rows)
    assert latest is not None and latest.date == date(2026, 11, 27)
    assert (latest.max_call_oi_strike, latest.max_put_oi_strike) == (50000.0, 49800.0)
    assert latest.weekly_max_call_oi_strike == 38800.0
    assert calc.latest_market_summary(DerivativesRows()) is None


def stock_day(day: date, oi: float, settlement: float | None = 100.0) -> StockFuturesDay:
    return StockFuturesDay(
        symbol="2330.TW",
        trading_date=day,
        underlying_code="2330",
        total_open_interest=oi,
        total_volume=10.0,
        open_interest_shares=int(oi * 2000),
        volume_shares=20_000,
        near_month="202612",
        near_settlement=settlement,
        near_volume=5.0,
        near_open_interest=oi,
        product_count=1,
        source="taifex",
        available_at=datetime(day.year, day.month, day.day, 10, tzinfo=UTC),
    )


def test_basis_and_oi_change() -> None:
    assert calc.basis_pct(101.0, 100.0) == pytest.approx(1.0)
    assert calc.basis_pct(99.0, 100.0) == pytest.approx(-1.0)
    assert calc.basis_pct(None, 100.0) is None
    assert calc.basis_pct(101.0, None) is None
    assert calc.basis_pct(101.0, 0.0) is None
    days = [date(2026, 11, 16) + timedelta(days=i) for i in range(12) if (i % 7) < 5]
    history = [stock_day(day, 1000.0 + 10 * i) for i, day in enumerate(days)]
    one, five = calc.oi_changes(history)
    assert one == pytest.approx(10 / 1080 * 100)
    assert five == pytest.approx((1090 - 1040) / 1040 * 100)
    assert calc.oi_changes(history[:1]) == (None, None)
    assert calc.oi_changes(history[:3])[1] is None  # fewer than 6 rows
    gap = [stock_day(date(2026, 10, 1), 1000.0), stock_day(date(2026, 11, 27), 1100.0)]
    assert calc.oi_changes(gap) == (None, None)  # a data hole is not a 1-session change
    assert calc.pct_change(5.0, 0.0) is None


# --- storage ----------------------------------------------------------------------------


async def test_rows_are_final_and_reads_are_point_in_time(fixture_rows) -> None:
    repo = MemoryQuantRepository()
    assert await repo.upsert_futures_institutional(fixture_rows.futures) == 18
    assert await repo.upsert_futures_institutional(fixture_rows.futures) == 0
    assert await repo.upsert_options_pc_ratio(fixture_rows.pc_ratio) == 3
    before_publication = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)  # day's rows are 10:00 UTC
    seen = await repo.derivatives_rows(before_publication, 10)
    assert {r.trading_date for r in seen.futures} == {date(2026, 11, 26)}
    assert await repo.latest_market_derivatives(datetime(2026, 11, 1, tzinfo=UTC)) is None
    latest = await repo.latest_market_derivatives(FAR)
    assert latest is not None and latest.date == date(2026, 11, 27)
    assert (await repo.foreign_tx_equiv_net_oi_on(date(2026, 11, 27))) == pytest.approx(-106699.2)
    assert await repo.foreign_tx_equiv_net_oi_on(date(2026, 11, 29)) is None


async def stock_repo() -> MemoryQuantRepository:
    repo = MemoryQuantRepository()
    table = specs()
    rows = src.build_stock_futures_days(
        src.parse_futures_daily(text("stock_futures.csv"), set(table)), table
    )
    # a previous and an older session so OI changes and basis can be computed
    older = [
        r.model_copy(
            update={
                "trading_date": date(2026, 11, 19) + timedelta(days=i),
                "total_open_interest": r.total_open_interest * (0.9 + 0.01 * i),
                "available_at": r.available_at - timedelta(days=7 - i),
            }
        )
        for r in rows
        for i in range(0, 7)
        if date(2026, 11, 19) + timedelta(days=i) < date(2026, 11, 26)
        and (date(2026, 11, 19) + timedelta(days=i)).weekday() < 5
    ]
    await repo.upsert_stock_futures([*older, *rows])
    await repo.upsert_market_stock_days(
        [
            MarketStockDay(
                symbol="2330.TW",
                market="TW",
                trading_date=date(2026, 11, 26),
                source="twse",
                name="台積電",
                open=2150.0,
                high=2184.0,
                low=2123.0,
                close=2150.0,
                volume=1,
                turnover_value=1.0,
                available_at=datetime(2026, 11, 26, 10, tzinfo=UTC),
            )
        ]
    )
    return repo


async def test_stock_futures_metrics_and_history() -> None:
    repo = await stock_repo()
    metrics = await repo.latest_stock_futures_metrics(FAR)
    tsmc = metrics["2330.TW"]
    assert tsmc.data_date == date(2026, 11, 26) and tsmc.near_month == "202612"
    assert tsmc.basis_pct == pytest.approx((2159 - 2150) / 2150 * 100)
    assert tsmc.oi_change_1d is not None and tsmc.oi_change_1d > 0
    assert tsmc.oi_change_5d is not None
    assert metrics["0050.TW"].basis_pct is None  # no spot close stored for the ETF
    history = await repo.stock_futures_history("2330.TW", FAR, 3)
    assert [h.trading_date for h in history] == sorted(h.trading_date for h in history)
    assert len(history) == 3
    # nothing is visible before the first publication
    assert await repo.latest_stock_futures_metrics(datetime(2026, 1, 1, tzinfo=UTC)) == {}


# --- scheduler ---------------------------------------------------------------------------


class FakeTaifex:
    def __init__(self, missing: set[str] | None = None, broken: set[str] | None = None) -> None:
        self.requests: list[tuple[str, dict | None]] = []
        self.missing = missing or set()  # urls answering with a block page
        self.broken = broken or set()  # urls raising

    async def fetch(self, url: str, form: dict[str, str] | None = None) -> str:
        self.requests.append((url, form))
        if url in self.broken:
            raise RuntimeError("TAIFEX answered 302")
        if url in self.missing:
            return "<html>404</html>"
        if url == src.FUT_INSTITUTIONAL_URL:
            return text("fut_institutional.csv")
        if url == src.PC_RATIO_URL:
            return text("pc_ratio.csv")
        if url == src.CALLS_PUTS_URL:
            return text("calls_puts.csv")
        if url == src.LARGE_TRADERS_URL:
            return text("large_traders.csv")
        if url == src.FUT_DAILY_URL:
            return text("stock_futures.csv")
        if url == src.OPT_DAILY_URL:
            return text("options_oi.csv")
        if url.endswith("202611new.txt"):
            return text("vix_202611.txt")
        if url == src.STOCK_LISTS_URL:
            return (FIXTURES / "stock_lists.html").read_text(encoding="utf-8")
        if url == src.SSF_ADJUSTED_URL:
            return (FIXTURES / "ssf_adjusted.json").read_text(encoding="utf-8")
        raise AssertionError(f"unexpected url {url}")

    def count(self, url: str) -> int:
        return Counter(u for u, _ in self.requests)[url]


@pytest.fixture(autouse=True)
def relaxed(monkeypatch):
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(sched, "MIN_SPECS", 1)
    monkeypatch.setattr(sched, "MIN_STOCK_FUTURES_DAY", 1)


async def run(repo, taifex, now, **kwargs):
    defaults = {"attempts": {}, "specs_cache": {}, "backfill_sessions": 2, "request_budget": 50}
    return await sched.refresh_derivatives_data(repo, taifex, now=now, **{**defaults, **kwargs})


def test_sessions_use_the_exchange_calendar() -> None:
    assert sched.is_session(date(2026, 11, 27)) and not sched.is_session(date(2026, 11, 28))
    assert not sched.is_session(date(2026, 12, 25))  # Constitution Day, a weekday closure
    assert sched.recent_sessions(date(2026, 11, 29), 3) == [
        date(2026, 11, 25),
        date(2026, 11, 26),
        date(2026, 11, 27),
    ]


async def test_live_day_stores_every_dataset_then_stops_asking() -> None:
    repo, taifex, attempts, cache = MemoryQuantRepository(), FakeTaifex(), {}, {}
    written = await run(repo, taifex, taipei(27, 18), attempts=attempts, specs_cache=cache)
    assert written["futures_institutional"] == 18 and written["options_pc_ratio"] == 2
    assert written["options_institutional"] == 12 and written["futures_large_traders"] == 4
    assert written["vix"] == 2 and written["options_oi_levels"] == 2
    assert written["stock_futures"] == 4  # the 26th only; the 27th is not in the report yet
    rows = await repo.derivatives_rows(FAR, 10)
    assert {r.trading_date for r in rows.futures} == {date(2026, 11, 26), date(2026, 11, 27)}
    # one range request per dataset (the whole two-day window), not one per day
    assert taifex.count(src.FUT_INSTITUTIONAL_URL) == 1
    assert taifex.count(src.STOCK_LISTS_URL) == 1 and taifex.count(src.SSF_ADJUSTED_URL) == 1
    form = next(f for u, f in taifex.requests if u == src.FUT_INSTITUTIONAL_URL)
    assert form["queryStartDate"] == "2026/11/26" and form["queryEndDate"] == "2026/11/27"
    # the live day of stock futures was incomplete: only that dataset is asked for again
    taifex.requests.clear()
    again = await run(repo, taifex, taipei(27, 18, 5), attempts=attempts, specs_cache=cache)
    assert again == {} and taifex.requests == []  # within the retry delay
    later = await run(repo, taifex, taipei(27, 19), attempts=attempts, specs_cache=cache)
    assert list(later) == ["stock_futures"] and later["stock_futures"] == 0
    assert [u for u, _ in taifex.requests] == [src.FUT_DAILY_URL]  # specs cached, one request


async def test_live_day_not_yet_published_is_retried_until_it_appears() -> None:
    repo, attempts = MemoryQuantRepository(), {}

    class Late(FakeTaifex):
        published = False

        async def fetch(self, url, form=None):
            body = await super().fetch(url, form)
            if url == src.PC_RATIO_URL and not self.published:
                return "\n".join(body.splitlines()[:1] + body.splitlines()[2:])  # drop the 27th
            return body

    taifex = Late()
    first = await run(repo, taifex, taipei(27, 15, 31), attempts=attempts)
    assert first["options_pc_ratio"] == 1  # 26th stored, 27th pending
    coverage = await repo.list_fetch_coverage(
        sched.SOURCE, "options_pc_ratio", "*", ["2026-11-26", "2026-11-27"]
    )
    assert set(coverage) == {"2026-11-26"}
    taifex.published = True
    assert (await run(repo, taifex, taipei(27, 15, 35), attempts=attempts)) == {}  # too soon
    fixed = await run(repo, taifex, taipei(27, 15, 50), attempts=attempts)
    assert fixed["options_pc_ratio"] == 1
    assert await repo.list_fetch_coverage(sched.SOURCE, "options_pc_ratio", "*", ["2026-11-27"])
    taifex.requests.clear()
    await run(repo, taifex, taipei(27, 16, 30), attempts=attempts)
    assert taifex.count(src.PC_RATIO_URL) == 0  # done for the day: never asked again


async def test_nothing_runs_during_the_trading_window_and_backfill_runs_off_session() -> None:
    repo, taifex = MemoryQuantRepository(), FakeTaifex()
    assert await run(repo, taifex, taipei(27, 10)) == {} and taifex.requests == []
    assert await run(repo, taifex, taipei(27, 15, 10)) == {}  # before the after-close time
    saturday = await run(repo, taifex, taipei(28, 11))
    assert saturday["futures_institutional"] == 18  # back-fill of the last sessions
    assert "options_oi_levels" not in saturday  # strikes are collected for the live day only
    assert {r.trading_date for r in (await repo.derivatives_rows(FAR, 10)).futures} == {
        date(2026, 11, 26),
        date(2026, 11, 27),
    }


async def test_backfill_disabled_still_fetches_the_live_day_only() -> None:
    repo, taifex = MemoryQuantRepository(), FakeTaifex()
    written = await run(repo, taifex, taipei(27, 18), backfill_sessions=0)
    form = next(f for u, f in taifex.requests if u == src.FUT_INSTITUTIONAL_URL)
    assert form["queryStartDate"] == form["queryEndDate"] == "2026/11/27"
    assert written["futures_institutional"] == 9
    assert await run(repo, FakeTaifex(), taipei(28, 11), backfill_sessions=0) == {}


async def test_request_budget_spreads_work_over_ticks() -> None:
    repo, taifex, attempts = MemoryQuantRepository(), FakeTaifex(), {}
    first = await run(repo, taifex, taipei(27, 18), attempts=attempts, request_budget=2)
    assert list(first) == ["futures_institutional", "options_pc_ratio"]
    second = await run(repo, taifex, taipei(27, 18, 1), attempts=attempts, request_budget=2)
    assert list(second) == ["options_institutional", "futures_large_traders"]


async def test_failures_are_isolated_remembered_and_never_raise() -> None:
    repo, attempts = MemoryQuantRepository(), {}
    taifex = FakeTaifex(broken={src.PC_RATIO_URL}, missing={src.LARGE_TRADERS_URL})
    written = await run(repo, taifex, taipei(27, 18), attempts=attempts)
    assert "options_pc_ratio" not in written and "futures_large_traders" not in written
    assert written["futures_institutional"] == 18 and written["vix"] == 2
    assert set(attempts) >= {"options_pc_ratio", "futures_large_traders"}
    assert not (await repo.derivatives_rows(FAR, 10)).pc_ratio
    taifex.requests.clear()
    await run(repo, taifex, taipei(27, 18, 5), attempts=attempts)
    assert taifex.count(src.PC_RATIO_URL) == 0  # backed off
    healthy = FakeTaifex()
    recovered = await run(repo, healthy, taipei(27, 18, 20), attempts=attempts)
    assert recovered["options_pc_ratio"] == 2 and recovered["futures_large_traders"] == 4


async def test_incomplete_underlying_list_blocks_stock_futures_only() -> None:
    repo, attempts = MemoryQuantRepository(), {}
    taifex = FakeTaifex()
    original = sched.MIN_SPECS
    try:
        sched.MIN_SPECS = 10_000
        written = await run(repo, taifex, taipei(27, 18), attempts=attempts)
    finally:
        sched.MIN_SPECS = original
    assert "stock_futures" not in written and "stock_futures" in attempts
    assert written["futures_institutional"] == 18


# --- market observations -----------------------------------------------------------------


def observation(day: date, *, market: str = "TW", futures: float = 0.0, source: str = "twse"):
    return MarketObservation(
        market=market,
        trading_date=day,
        index_close=20000.0,
        total_issues=1000,
        advancing_issues=500,
        declining_issues=400,
        unchanged_issues=100,
        above_ma20_issues=500,
        above_ma60_issues=450,
        new_high_52w_issues=50,
        new_low_52w_issues=10,
        up_volume=1.0,
        down_volume=1.0,
        turnover_value=3e11,
        foreign_net_flow=1.0,
        investment_trust_net_flow=1.0,
        dealer_net_flow=1.0,
        futures_net_open_interest=futures,
        margin_balance=1.0,
        short_balance=1.0,
        available_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=10),
        source=source,
    )


async def test_observations_get_a_new_revision_with_the_real_position() -> None:
    repo = MemoryQuantRepository()
    await repo.upsert_market_observations(
        [observation(date(2026, 11, 26)), observation(date(2026, 11, 27))]
    )
    await repo.upsert_market_observations([observation(date(2026, 11, 27), market="TWO")])
    await repo.upsert_market_observations(
        [
            observation(date(2026, 11, 27), source="synthetic_demo", futures=123.0).model_copy(
                update={"market": "TW"}
            )
        ]
    )
    written = await run(repo, FakeTaifex(), taipei(27, 18))
    assert written["futures_institutional"] == 18
    stored = await repo.list_market_observations("TW")
    latest = {o.trading_date: o for o in stored if o.source == "twse" and o.revision == 2}
    assert latest[date(2026, 11, 27)].futures_net_open_interest == pytest.approx(-106699.2)
    assert latest[date(2026, 11, 26)].futures_net_open_interest == pytest.approx(-106503.35)
    assert [
        o.revision for o in stored if o.source == "twse" and o.trading_date == date(2026, 11, 27)
    ] == [1, 2]
    # the original is kept; as-of availability is unchanged (the documented publication time)
    original = next(
        o
        for o in stored
        if o.source == "twse" and o.trading_date == date(2026, 11, 27) and o.revision == 1
    )
    assert latest[date(2026, 11, 27)].available_at == original.available_at
    assert original.futures_net_open_interest == 0.0
    synthetic = [o for o in stored if o.source == "synthetic_demo"]
    assert [o.revision for o in synthetic] == [1] and synthetic[
        0
    ].futures_net_open_interest == 123.0
    assert [o.futures_net_open_interest for o in await repo.list_market_observations("TWO")] == [
        0.0
    ]
    # idempotent: nothing left to revise
    assert await refresh_observation_futures(repo) == 0
    assert await refresh_observation_futures(repo, market="TWO") == 0


async def test_new_observations_read_the_stored_position() -> None:
    from quant_signal.application.market_observations import MarketObservationSyncService

    repo = MemoryQuantRepository()
    await repo.upsert_futures_institutional(
        src.parse_futures_institutional(text("fut_institutional.csv"))
    )
    service = MarketObservationSyncService(repo, object(), market="TW", source="twse")  # type: ignore[arg-type]
    assert await service._foreign_futures_net_oi(date(2026, 11, 27)) == pytest.approx(-106699.2)
    assert await service._foreign_futures_net_oi(date(2026, 11, 20)) == 0.0
    other = MarketObservationSyncService(repo, object(), market="TWO", source="tpex")  # type: ignore[arg-type]
    assert await other._foreign_futures_net_oi(date(2026, 11, 27)) == 0.0


def test_market_environment_handles_real_position_magnitudes(healthy_market) -> None:
    engine = MarketEnvironmentEngine()
    base = engine.analyze(healthy_market, as_of=healthy_market[-1].trading_date)

    def with_futures(values: list[float]) -> list[MarketObservation]:
        return [
            item.model_copy(update={"futures_net_open_interest": value})
            for item, value in zip(healthy_market, values, strict=True)
        ]

    count = len(healthy_market)
    # foreign TX-equivalent net OI: roughly -100,000 contracts, drifting a few thousand a day
    shorts = [-100_000.0 + 1_500.0 * ((i * 7) % 11 - 5) for i in range(count)]
    covering = [*shorts[:-1], shorts[-2] + 12_000.0]  # latest day covers 12k contracts
    adding = [*shorts[:-1], shorts[-2] - 12_000.0]

    def flow(values: list[float]) -> float:
        snapshot = engine.analyze(with_futures(values), as_of=healthy_market[-1].trading_date)
        return snapshot.factors.institutional_flow

    assert flow(covering) > flow(shorts) > flow(adding)
    assert 0 <= flow(adding) <= 100 and 0 <= flow(covering) <= 100
    # no TAIFEX data stored (all 0): the futures leg is neutral and the score stays defined
    assert with_futures([0.0] * count)[-1].futures_net_open_interest == 0
    neutral = engine.analyze(with_futures([0.0] * count), as_of=healthy_market[-1].trading_date)
    assert 0 <= neutral.factors.institutional_flow <= 100
    # a missing latest day (0) amid real history does not become a -100,000 outlier
    missing_latest = flow([*shorts[:-1], 0.0])
    assert abs(missing_latest - flow([0.0] * count)) < 1e-9
    assert base.market == "TW"


# --- API ---------------------------------------------------------------------------------


@pytest.fixture
async def api_repo() -> MemoryQuantRepository:
    repo = await stock_repo()
    await run(repo, FakeTaifex(), taipei(27, 18))
    await repo.upsert_market_stock_days(
        [
            MarketStockDay(
                symbol="1101.TW",
                market="TW",
                trading_date=date(2026, 11, 26),
                source="twse",
                name="台泥",
                open=30.0,
                high=31.0,
                low=29.0,
                close=30.0,
                volume=1,
                turnover_value=1.0,
                available_at=datetime(2026, 11, 26, 10, tzinfo=UTC),
            )
        ]
    )
    return repo


async def test_services(api_repo) -> None:
    market = await MarketDerivativesService(api_repo).get(as_of=date(2026, 11, 28), days=60)
    assert market.data_through == date(2026, 11, 27) and market.days[-1].vix == 51.94
    assert market.latest is not None and market.latest.max_call_oi_strike == 50000.0
    assert market.notes
    early = await MarketDerivativesService(api_repo).get(as_of=date(2026, 11, 1))
    assert early.days == [] and early.latest is None and early.data_through is None
    stock = await StockFuturesService(api_repo).get("2330", as_of=date(2026, 11, 28))
    assert stock.symbol == "2330.TW" and stock.has_futures
    assert stock.days[-1].basis_pct == pytest.approx((2159 - 2150) / 2150 * 100)
    assert stock.oi_change_1d is not None
    none = await StockFuturesService(api_repo).get("1101.TW", as_of=date(2026, 11, 28))
    assert none.has_futures is False and none.days == [] and none.notes
    with pytest.raises(StockNotFound):
        await StockFuturesService(api_repo).get("9998", as_of=date(2026, 11, 28))


def test_endpoints(api_repo) -> None:
    app.dependency_overrides[get_repository] = lambda: api_repo
    app.dependency_overrides[require_principal] = lambda: Principal("anonymous")
    try:
        client = TestClient(app)
        response = client.get("/api/v1/market/derivatives?as_of=2026-11-28&days=5")
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"as_of", "data_through", "days", "latest", "notes"}
        assert body["data_through"] == "2026-11-27"
        assert set(body["days"][-1]) == {
            "date",
            "foreign_tx_net_oi",
            "foreign_tx_equiv_net_oi",
            "foreign_net_oi_change",
            "trust_tx_net_oi",
            "dealer_tx_net_oi",
            "pc_volume_ratio",
            "pc_oi_ratio",
            "foreign_call_net_oi",
            "foreign_put_net_oi",
            "top5_net_oi",
            "top10_net_oi",
            "vix",
        }
        assert body["latest"]["max_put_oi_strike"] == 49800.0
        empty = client.get("/api/v1/market/derivatives?as_of=2026-01-01").json()
        assert empty["days"] == [] and empty["latest"] is None
        assert client.get("/api/v1/market/derivatives?days=0").status_code == 422
        stock = client.get("/api/v1/stocks/2330.TW/futures?as_of=2026-11-28")
        assert stock.status_code == 200, stock.text
        payload = stock.json()
        assert payload["has_futures"] is True and payload["days"][-1]["near_month"] == "202612"
        assert set(payload["days"][-1]) == {
            "date",
            "open_interest",
            "volume",
            "near_month",
            "near_settlement",
            "basis_pct",
        }
        assert client.get("/api/v1/stocks/9998/futures").status_code == 404
    finally:
        app.dependency_overrides.clear()


async def test_mcp_tool_is_registered() -> None:
    from quant_signal.adapters.mcp import server

    tools = {tool.name for tool in await server.mcp.list_tools()}
    assert "get_market_derivatives" in tools


# --- migration and Postgres statements ----------------------------------------------------

MIGRATION = Path(__file__).parents[1] / "migrations" / "versions" / "0020_derivatives_data.py"
TABLES = (
    "futures_institutional_daily",
    "options_pc_ratio_daily",
    "options_institutional_daily",
    "futures_large_traders_daily",
    "stock_futures_daily",
    "options_oi_levels_daily",
    "vix_daily",
)


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
    spec = importlib.util.spec_from_file_location("migration_0020", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    recorder = _Recorder()
    module.op = recorder
    assert (
        module.revision == "0020_derivatives_data" and module.down_revision == "0017_holders_data"
    )
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


class _Result:
    rowcount = 0

    def scalars(self):
        return self

    def all(self):
        return []

    def first(self):
        return None

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


async def test_postgres_statements_compile_with_point_in_time_filters(fixture_rows) -> None:
    from quant_signal.infrastructure.db.repository import PostgresQuantRepository

    session = _StubSession()
    repo = PostgresQuantRepository(session)  # type: ignore[arg-type]
    await repo.derivatives_rows(FAR, 61)
    await repo.stock_futures_history("2330.TW", FAR, 65)
    await repo.latest_stock_futures_days(FAR)
    await repo.upsert_futures_institutional(fixture_rows.futures)
    await repo.upsert_vix_daily(fixture_rows.vix)
    selects = [sql for sql in session.sql if sql.startswith("SELECT")]
    assert selects and all("available_at <=" in sql for sql in selects)
    assert sum("ON CONFLICT DO NOTHING" in sql for sql in session.sql) == 2


# --- real PostgreSQL ------------------------------------------------------------------------

DATABASE_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
TEST_SOURCE = "taifex_test"


@pytest.fixture
async def pg_session():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def clean() -> None:
        async with factory() as session:
            for table in TABLES:
                await session.execute(
                    text(f"DELETE FROM {table} WHERE source = :source"), {"source": TEST_SOURCE}
                )
            await session.execute(
                text("DELETE FROM market_observations_daily WHERE source = :s"), {"s": TEST_SOURCE}
            )
            await session.execute(
                text("DELETE FROM market_stock_daily WHERE source = :s"), {"s": TEST_SOURCE}
            )
            await session.commit()

    await clean()
    async with factory() as session:
        yield session
    await clean()
    await engine.dispose()


@pytest.mark.skipif(not DATABASE_URL, reason="QUANT_TEST_DATABASE_URL not set")
async def test_postgres_round_trip(pg_session) -> None:
    from quant_signal.infrastructure.db import PostgresQuantRepository

    repo = PostgresQuantRepository(pg_session)

    def mine(rows):
        return [row.model_copy(update={"source": TEST_SOURCE}) for row in rows]

    futures = mine(src.parse_futures_institutional(text("fut_institutional.csv")))
    assert await repo.upsert_futures_institutional(futures) == 18
    assert await repo.upsert_futures_institutional(futures) == 0  # published rows are final
    assert await repo.upsert_options_pc_ratio(mine(src.parse_pc_ratio(text("pc_ratio.csv")))) == 3
    options = mine(src.parse_options_institutional(text("calls_puts.csv")))
    assert await repo.upsert_options_institutional(options) == 12
    large = mine(src.parse_large_traders(text("large_traders.csv")))
    assert await repo.upsert_futures_large_traders(large) == 4
    levels = mine(src.options_oi_levels(src.parse_options_daily(text("options_oi.csv"))))
    assert await repo.upsert_options_oi_levels(levels) == 2
    assert await repo.upsert_vix_daily(mine(src.parse_vix(text("vix_202611.txt")))) > 0
    table = specs()
    stock = mine(
        src.build_stock_futures_days(
            src.parse_futures_daily(text("stock_futures.csv"), set(table)), table
        )
    )
    assert await repo.upsert_stock_futures(stock) == 4

    rows = await repo.derivatives_rows(FAR, 60)
    mine_only = [r for r in rows.futures if r.source == TEST_SOURCE]
    assert {r.trading_date for r in mine_only} >= {date(2026, 11, 26), date(2026, 11, 27)}
    before = await repo.derivatives_rows(datetime(2026, 11, 27, 9, tzinfo=UTC), 60)
    assert date(2026, 11, 27) not in {r.trading_date for r in before.futures}
    latest = await repo.latest_market_derivatives(FAR)
    assert latest is not None and latest.max_call_oi_strike is not None
    series = await repo.foreign_tx_equiv_net_oi_series(date(2026, 11, 26), date(2026, 11, 27))
    assert series[date(2026, 11, 27)] == pytest.approx(-106699.2)

    await repo.upsert_market_stock_days(
        [
            MarketStockDay(
                symbol="2330.TW",
                market="TW",
                trading_date=date(2026, 11, 26),
                source=TEST_SOURCE,
                name="台積電",
                open=2150.0,
                high=2184.0,
                low=2123.0,
                close=2150.0,
                volume=1,
                turnover_value=1.0,
                available_at=datetime(2026, 11, 26, 10, tzinfo=UTC),
            )
        ]
    )
    metrics = await repo.latest_stock_futures_metrics(FAR)
    assert metrics["2330.TW"].basis_pct == pytest.approx((2159 - 2150) / 2150 * 100)
    history = await repo.stock_futures_history("2330.TW", FAR, 5)
    assert history and history[-1].near_month == "202612"
    assert await repo.has_stock_futures("2330.TW") and not await repo.has_stock_futures("1101.TW")
    spot = await repo.stock_spot_closes("2330.TW", date(2026, 11, 1), date(2026, 11, 30))
    assert spot[date(2026, 11, 26)] == 2150.0

    await repo.upsert_market_observations([observation(date(2026, 11, 27), source=TEST_SOURCE)])
    assert await refresh_observation_futures(repo) >= 1
    stored = [o for o in await repo.list_market_observations("TW") if o.source == TEST_SOURCE]
    assert [o.revision for o in stored] == [1, 2]
    assert stored[-1].futures_net_open_interest == pytest.approx(-106699.2)
    assert await refresh_observation_futures(repo) == 0
