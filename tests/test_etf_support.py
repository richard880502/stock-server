"""ETF support: payload parsers, ingestion, paper trading, search, overview, dividends.

Parsers run on trimmed real payloads of 2026-11-27 (tests/fixtures/etf); nothing here
touches the network.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from paper_support import (
    FRI,
    MON,
    THU,
    Clock,
    at,
    bar,
    etf_row,
    market_repo,
    new_user,
    quote,
    stock_row,
)

from quant_signal.application.market_sync import (
    ETF_BACKFILL_DAYS,
    ETF_DAY_DATASET,
    MARKET_STOCK_DATASET,
    MarketSnapshotSyncService,
)
from quant_signal.application.paper_jobs import PaperJobs
from quant_signal.application.paper_market import load_market, past_references
from quant_signal.application.paper_trading import PaperService, paper_rules
from quant_signal.application.services import InstrumentSearchService
from quant_signal.application.stocks import StockNotFound, StockOverviewService
from quant_signal.domain.etf import EtfDay, is_etf_code, is_etf_symbol
from quant_signal.domain.models import MarketStockDay
from quant_signal.domain.paper import AccountCreate, OrderRequest
from quant_signal.domain.tw_trading import (
    BOND_ETF_TAX_EXEMPT_THROUGH,
    fill_cost,
    is_bond_etf,
    tick_size,
    transaction_tax,
)
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.infrastructure.providers import short_data as short_src
from quant_signal.infrastructure.providers.tpex import TpexProvider
from quant_signal.infrastructure.providers.twse import TwseProvider
from quant_signal.quant.paper_matching import compute_fill

FIXTURES = Path(__file__).parent / "fixtures" / "etf"
D = Decimal


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


TWSE_PAYLOAD = load("twse_mi_index_20261127.json")
TPEX_PAYLOAD = load("tpex_daily_quotes_20261127.json")
DAY = date(2026, 11, 27)


# --------------------------------------------------------------------- inclusion rule


def test_inclusion_rule_keeps_etfs_and_drops_everything_else() -> None:
    for code in ("0050", "0056", "00878", "006208", "00631L", "00632R", "00679B", "00400A"):
        assert is_etf_code(code), code
    for code in ("2330", "1101B", "1312A", "01001T", "020000", "02001L", "910322", "0", "00"):
        assert not is_etf_code(code), code
    assert is_etf_symbol("0050.TW") and is_etf_symbol("00679B.TWO") and not is_etf_symbol("2330.TW")


# ------------------------------------------------------------------------- the parsers


def test_twse_etf_rows_cover_every_code_shape_and_skip_no_trade_and_non_etfs() -> None:
    rows = {r.symbol: r for r in TwseProvider.parse_etf_day(TWSE_PAYLOAD, DAY)}
    assert set(rows) == {
        "00400A.TW",  # active ETF
        "00403A.TW",
        "0050.TW",  # 4-digit
        "0056.TW",
        "00631L.TW",  # leveraged
        "00632R.TW",  # inverse
        "006208.TW",  # 6-digit
        "00878.TW",  # 5-digit
    }
    # no-trade ``--`` row (00707R), ETNs (020000, 02001L, 020011, 020038), 受益證券 (01001T),
    # special shares (1101B, 1312A), depositary receipts (910322) and stocks are not ETFs
    etf = rows["0050.TW"]
    assert (etf.name, etf.market, etf.trading_date) == ("元大台灣50", "TW", DAY)
    assert (etf.open, etf.high, etf.low, etf.close) == (92.8, 93.0, 91.6, 92.4)
    assert (etf.change, etf.volume, etf.trades) == (-0.45, 111_385_656, 103_240)
    assert etf.turnover_value == 10_289_071_787
    assert rows["00632R.TW"].change == pytest.approx(0.06)  # red "+" = up
    assert rows["00878.TW"].change == pytest.approx(-0.26)
    assert all(r.source == "twse_official" for r in rows.values())


def test_tpex_etf_rows_and_no_trade_rows() -> None:
    rows = {r.symbol: r for r in TpexProvider.parse_etf_day(TPEX_PAYLOAD, DAY)}
    assert set(rows) == {
        "006201.TWO",
        "00411A.TWO",
        "00679B.TWO",  # bond
        "00687B.TWO",
        "00687C.TWO",  # other currency class
        "00719B.TWO",
        "00734B.TWO",
        "00877.TWO",
        "00980D.TWO",  # active bond
    }
    bond = rows["00679B.TWO"]
    assert (bond.close, bond.change, bond.volume, bond.trades) == (26.53, -0.11, 13_359_941, 1_832)
    assert bond.turnover_value == 355_101_397 and bond.market == "TWO"
    assert rows["00734B.TWO"].change == 0.0  # "0.00 " prints as flat
    assert rows["00877.TWO"].change == pytest.approx(2.18)
    # 00870B printed ``---`` (no trade), 02xxxx are ETNs, 8349A is a special share
    assert "00870B.TWO" not in rows and not any(s.startswith("02") for s in rows)


def test_a_report_for_another_date_yields_no_etf_rows() -> None:
    assert TwseProvider.parse_etf_day(TWSE_PAYLOAD, date(2026, 11, 26)) == []
    assert TpexProvider.parse_etf_day(TPEX_PAYLOAD, date(2026, 11, 26)) == []
    assert TwseProvider.parse_etf_day({"stat": "很抱歉，沒有符合條件的資料!"}, DAY) == []


def test_stock_parsing_is_unchanged_by_the_etf_rule() -> None:
    twse = TwseProvider.parse_daily_market(TWSE_PAYLOAD)
    assert twse is not None
    assert sorted(q.symbol for q in twse.quotes) == ["2317.TW", "2330.TW"]
    tpex = TpexProvider.parse_daily_quotes(TPEX_PAYLOAD)
    assert tpex is not None
    assert sorted(q.symbol for q in tpex[1]) == ["5347.TWO", "6488.TWO"]


def _transport(counter: list[str]):
    twse_flow = {"stat": "OK", "data": []}

    def handler(request: httpx.Request) -> httpx.Response:
        counter.append(request.url.path)
        if request.url.path.endswith("MI_INDEX") or request.url.path.endswith("dailyQuotes"):
            body = TWSE_PAYLOAD if "twse" in request.url.host else TPEX_PAYLOAD
            return httpx.Response(200, json=body)
        if request.url.path.endswith("MI_MARGN") or request.url.path.endswith("T86"):
            return httpx.Response(200, json=twse_flow)
        return httpx.Response(200, json={"tables": [{"data": []}]})

    return httpx.MockTransport(handler)


async def test_one_download_serves_both_stock_and_etf_rows() -> None:
    calls: list[str] = []
    async with TwseProvider(
        client=httpx.AsyncClient(transport=_transport(calls), base_url="https://www.twse.com.tw"),
        request_interval_seconds=0,
    ) as twse:
        stocks, etfs = await twse.fetch_market_day_rows(DAY)
        only_stocks = await twse.fetch_market_stock_day(DAY)
    assert calls.count("/rwd/zh/afterTrading/MI_INDEX") == 2  # one per call, not two
    assert sorted(s.symbol for s in stocks) == ["2317.TW", "2330.TW"]
    assert stocks == only_stocks  # the stock rows are exactly what they were
    assert len(etfs) == 8
    calls.clear()
    async with TpexProvider(
        client=httpx.AsyncClient(transport=_transport(calls), base_url="https://www.tpex.org.tw"),
        request_interval_seconds=0,
    ) as tpex:
        stocks, etfs = await tpex.fetch_market_day_rows(DAY)
        extra = await tpex.fetch_market_etf_day(DAY)
    assert calls.count("/www/zh-tw/afterTrading/dailyQuotes") == 2
    assert sorted(s.symbol for s in stocks) == ["5347.TWO", "6488.TWO"]
    assert extra == etfs and len(etfs) == 9


# ---------------------------------------------------------------------------- ingestion


def _etf(symbol: str, day: date, close: float = 30.0) -> EtfDay:
    return etf_row(symbol, day, close=close, name=f"etf {symbol}")


class EtfProvider:
    """Stock + ETF provider double: trades every weekday except ``holidays``."""

    def __init__(self, holidays: set[date] | None = None) -> None:
        self.holidays = holidays or set()
        self.full: list[date] = []
        self.etf_only: list[date] = []

    def _trades(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def _stocks(self, day: date) -> list[MarketStockDay]:
        if not self._trades(day):
            return []
        return [stock_row("1101.TW", day, close=10.0, name="台泥")]

    async def fetch_market_stock_day(self, day: date):
        return self._stocks(day)

    async def fetch_market_day_rows(self, day: date):
        self.full.append(day)
        return self._stocks(day), await self._etfs(day)

    async def fetch_market_etf_day(self, day: date):
        self.etf_only.append(day)
        return await self._etfs(day)

    async def _etfs(self, day: date):
        return [_etf("0050.TW", day), _etf("00679B.TWO", day)] if self._trades(day) else []


def _service(repo, provider, now: dict):
    return MarketSnapshotSyncService(
        repo, provider, market="TW", source="twse_official", clock=lambda: now["value"]
    )


SAT_EVENING = datetime(2026, 11, 28, 8, 0, tzinfo=UTC)  # Saturday 16:00 Taipei
NOV16_TO_27 = (date(2026, 11, 16), date(2026, 11, 27))


async def test_stock_loop_stores_etf_rows_from_the_same_payload() -> None:
    repo, provider, now = MemoryQuantRepository(), EtfProvider(), {"value": SAT_EVENING}
    report = await _service(repo, provider, now).sync(start=date(2026, 11, 23), end=DAY)
    assert provider.etf_only == []  # no extra request: every ETF row rode along
    assert sorted(provider.full) == [date(2026, 11, d) for d in (23, 24, 25, 26, 27)]
    assert report.market_observations_inserted == 5  # stock rows, unchanged semantics
    assert len(repo.etf_days) == 5 * 2  # two ETFs per day
    assert {s for s, _ in repo.etf_days} == {"0050.TW", "00679B.TWO"}
    coverage = await repo.list_fetch_coverage(
        "twse_official", ETF_DAY_DATASET, "TW", ["2026-11-27"]
    )
    assert coverage["2026-11-27"].status == "ok" and coverage["2026-11-27"].row_count == 2


async def test_etf_rows_never_enter_the_stock_table_or_change_stock_coverage() -> None:
    repo, provider = MemoryQuantRepository(), EtfProvider()
    now = {"value": datetime(2026, 11, 27, 9, 0, tzinfo=UTC)}  # 17:00 on the day itself
    await _service(repo, provider, now).sync(start=DAY, end=DAY)
    assert {k[0] for k in repo.market_stock_days} == {"1101.TW"}
    stock_cov = await repo.list_fetch_coverage(
        "twse_official", MARKET_STOCK_DATASET, "TW", ["2026-11-27"]
    )
    assert stock_cov["2026-11-27"].dataset == MARKET_STOCK_DATASET
    # Fetched on the day itself (before it ended): the stock rule refetches next day, as before.
    provider.full.clear()
    await _service(repo, provider, now).sync(start=DAY, end=DAY)
    assert provider.full == [DAY]


async def test_days_with_stored_stock_rows_get_a_bounded_resumable_etf_backfill() -> None:
    repo, now = MemoryQuantRepository(), {"value": SAT_EVENING}
    # three weeks of stock rows exist already (an old deploy without ETF support)
    legacy = FakeStockOnly()
    await _service(repo, legacy, now).sync(start=date(2026, 11, 9), end=DAY)
    assert not repo.etf_days

    provider = EtfProvider()
    service = _service(repo, provider, now)
    first = await service.sync(start=date(2026, 11, 9), end=DAY)
    assert provider.full == []  # the stock days were not refetched
    assert len(provider.etf_only) == ETF_BACKFILL_DAYS == 10  # bounded, newest first
    assert provider.etf_only == sorted(provider.etf_only, reverse=True)
    assert provider.etf_only[0] == DAY
    assert any("ETF" in note for note in first.notes)
    newest = {d for _, d in repo.etf_days}
    assert len(newest) == 10 and DAY in newest  # >= 5 trading days after the first run

    provider.etf_only.clear()
    await service.sync(start=date(2026, 11, 9), end=DAY)  # resumes with the rest
    assert provider.etf_only == [date(2026, 11, d) for d in (13, 12, 11, 10, 9)]
    provider.etf_only.clear()
    await service.sync(start=date(2026, 11, 9), end=DAY)
    assert provider.etf_only == []  # once


class FakeStockOnly:
    """A provider without ETF support (the pre-ETF behaviour)."""

    async def fetch_market_stock_day(self, day: date):
        return [stock_row("1101.TW", day, close=10.0, name="台泥")] if day.weekday() < 5 else []


async def test_backfill_skips_holidays_and_never_runs_in_session() -> None:
    holiday = date(2026, 11, 25)
    repo, now = MemoryQuantRepository(), {"value": SAT_EVENING}
    await _service(repo, FakeStockOnly(), now).sync(start=date(2026, 11, 23), end=DAY)
    repo.fetch_coverage[("twse_official", MARKET_STOCK_DATASET, "TW", holiday.isoformat())] = (
        repo.fetch_coverage[("twse_official", MARKET_STOCK_DATASET, "TW", "2026-11-24")].model_copy(
            update={"period": holiday.isoformat(), "status": "empty", "row_count": 0}
        )
    )
    provider = EtfProvider(holidays={holiday})
    # 10:00 Taipei on a trading day: nothing is requested
    now["value"] = datetime(2026, 11, 27, 2, 0, tzinfo=UTC)
    await _service(repo, provider, now).sync(start=date(2026, 11, 23), end=DAY)
    assert provider.etf_only == []
    now["value"] = SAT_EVENING
    await _service(repo, provider, now).sync(start=date(2026, 11, 23), end=DAY)
    assert holiday not in provider.etf_only and len(provider.etf_only) == 4


async def test_available_at_follows_the_publication_time_convention() -> None:
    repo, provider, now = MemoryQuantRepository(), EtfProvider(), {}
    # fetched on the day itself, 15:30 Taipei: that is when we knew it
    now["value"] = datetime(2026, 11, 27, 7, 30, tzinfo=UTC)
    await _service(repo, provider, now).sync(start=DAY, end=DAY)
    assert repo.etf_days[("0050.TW", DAY)].available_at == now["value"]
    # a later back-fill is stamped with the publication time (18:00 Taipei)
    other = date(2026, 11, 26)
    now["value"] = SAT_EVENING
    await _service(repo, provider, now).sync(start=other, end=other)
    assert repo.etf_days[("0050.TW", other)].available_at == datetime(
        2026, 11, 26, 10, 0, tzinfo=UTC
    )


# ------------------------------------------------------------------- rules audit


def test_etf_tick_bands_and_tax_rate() -> None:
    assert tick_size(49.99, etf=True) == D("0.01") and tick_size(50, etf=True) == D("0.05")
    assert tick_size(49.99) == D("0.05")  # a stock at the same price uses the stock band
    # 0050: 0.1% on a sell; stock at the same amount pays 0.3%
    assert transaction_tax(160_000, symbol="0050.TW") == 160
    assert transaction_tax(160_000, symbol="2330.TW") == 480


def test_bond_etf_tax_exemption_flips_after_the_end_date() -> None:
    assert is_bond_etf("00679B.TWO") and not is_bond_etf("0050.TW") and not is_bond_etf("2330B.TW")
    assert BOND_ETF_TAX_EXEMPT_THROUGH == date(2026, 12, 31)
    kwargs = {"symbol": "00679B.TWO"}
    assert transaction_tax(1_000_000, trade_date=date(2026, 12, 31), **kwargs) == 0
    assert transaction_tax(1_000_000, trade_date=date(2027, 1, 4), **kwargs) == 1000
    assert transaction_tax(1_000_000, **kwargs) == 1000  # no date: never assume the exemption
    sold = compute_fill("sell", 1000, D("26.5"), symbol="00679B.TWO", trade_date=date(2026, 11, 30))
    assert sold.tax == 0 and sold.commission == 37
    later = compute_fill("sell", 1000, D("26.5"), symbol="00679B.TWO", trade_date=date(2027, 1, 4))
    assert later.tax == 26
    assert fill_cost("sell", 26_500, symbol="00679B.TWO", trade_date=date(2026, 12, 1)) == 37
    # an ordinary 0050 sell is taxed whatever the date
    assert transaction_tax(160_000, symbol="0050.TW", trade_date=date(2026, 11, 30)) == 160


def test_rules_text_states_the_bond_exemption_and_the_limit_limitation() -> None:
    rules = paper_rules(1, 3)
    text = "\n".join(rules["notes"])
    assert rules["bond_etf_tax_exempt_through"] == "2026-12-31"
    assert "債券 ETF" in text and "2026-12-31" in text
    assert "0050" in text and "無法由代號分辨" in text


# ------------------------------------------------------------------- paper trading


def req(symbol: str, side="buy", kind="market", lots=1, limit=None) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        side=side,
        order_type=kind,
        limit_price=None if limit is None else D(limit),
        lots=lots,
    )


async def paper_setup(now):
    repo = await market_repo()
    await repo.upsert_etf_days(
        [
            etf_row("00679B.TWO", d, close=26.5, name="元大美債20年", change=-0.1)
            for d in (date(2026, 11, 25), THU)
        ]
    )
    clock = Clock(now)
    service = PaperService(repo, clock=clock)
    user = new_user()
    summary = await service.create_account(
        user, AccountCreate(name="主帳戶", initial_cash=2_000_000)
    )
    return repo, clock, service, PaperJobs(), user, summary.id


async def test_market_view_knows_etfs_from_etf_daily_only() -> None:
    repo, *_ = await paper_setup(at(FRI, 10, 0))
    views = await load_market(repo, ["0050.TW", "00679B.TWO", "1101.TW", "00878.TW"])
    assert views["0050.TW"].known and views["0050.TW"].name == "元大台灣50"
    assert views["00679B.TWO"].official_close == D("26.50")
    assert views["00679B.TWO"].official_prev == D("26.60")  # close - change
    assert views["1101.TW"].known and not views["00878.TW"].known
    assert "0050.TW" not in {k[0] for k in repo.market_stock_days}
    refs = await past_references(repo, ["00679B.TWO", "0050.TW"], THU)
    assert refs == {"00679B.TWO": D("26.60"), "0050.TW": D("150.00")}


async def test_etf_preview_tick_band_and_unknown_message() -> None:
    repo, _, service, _, user, acct = await paper_setup(at(FRI, 10, 0))
    repo.quotes_latest["00679B.TWO"] = quote("00679B.TWO", 26.5, at(FRI, 9, 59), reference=26.6)
    off = await service.preview(user, acct, req("0050.TW", kind="limit", limit="150.03"))
    assert not off.ok and "升降單位" in off.reject_reason
    ok = await service.preview(user, acct, req("00679B.TWO", kind="limit", limit="26.5"))
    assert ok.ok and ok.tick == 0.01 and ok.limit_up is None  # no price limit is guessed
    etf50 = await service.preview(user, acct, req("0050", kind="limit", limit="150.05"))
    assert etf50.ok and etf50.symbol == "0050.TW" and etf50.name == "元大台灣50"
    assert etf50.tick == 0.05
    bad = await service.preview(user, acct, req("00999.TW"))
    assert not bad.ok and "找不到股票" in bad.reject_reason and "ETF" in bad.reject_reason


async def test_bare_etf_codes_resolve_through_search() -> None:
    _, _, service, _, user, acct = await paper_setup(at(FRI, 10, 0))
    p = await service.preview(user, acct, req("00679B", kind="limit", limit="26.5"))
    assert p.symbol == "00679B.TWO"


async def test_bond_etf_sell_pays_no_tax_but_a_normal_etf_does() -> None:
    repo, clock, service, jobs, user, acct = await paper_setup(at(FRI, 10, 0))
    for symbol, ref in (("00679B.TWO", 26.6), ("0050.TW", 150.0)):
        repo.quotes_latest[symbol] = quote(symbol, ref, at(FRI, 9, 59), reference=ref)
        await service.place_order(user, acct, req(symbol))
        repo.quotes_latest[symbol] = quote(symbol, ref, at(FRI, 10, 0, 5), reference=ref)
    await jobs.tick(repo, at(FRI, 10, 0, 6))
    clock.now = at(MON, 10, 0)
    for symbol, ref in (("00679B.TWO", 26.8), ("0050.TW", 160.0)):
        repo.quotes_latest[symbol] = quote(symbol, ref, at(MON, 9, 59), reference=ref)
        await service.place_order(user, acct, req(symbol, side="sell"))
        repo.quotes_latest[symbol] = quote(symbol, ref, at(MON, 10, 0, 5), reference=ref)
    await jobs.tick(repo, at(MON, 10, 0, 6))
    sells = {f.symbol: f for f in (await service.list_fills(user, acct)).fills if f.side == "sell"}
    assert sells["00679B.TWO"].tax == 0
    assert sells["0050.TW"].tax == int(sells["0050.TW"].amount * 0.001)


async def test_snapshot_values_an_etf_position_at_its_official_close() -> None:
    repo, clock, service, jobs, user, acct = await paper_setup(at(FRI, 10, 0))
    repo.quotes_latest["0050.TW"] = quote("0050.TW", 150, at(FRI, 9, 59), reference=150)
    await service.place_order(user, acct, req("0050.TW"))
    repo.quotes_latest["0050.TW"] = quote("0050.TW", 150, at(FRI, 10, 0, 5), reference=150)
    await jobs.tick(repo, at(FRI, 10, 0, 6))
    await repo.upsert_etf_days([etf_row("0050.TW", FRI, close=152.0, name="元大台灣50")])
    clock.now = at(FRI, 15, 0)
    await jobs.tick(repo, at(FRI, 15, 0))
    snap = (await repo.list_paper_snapshots(acct, epoch=0))[0]
    assert snap.trading_date == FRI and snap.market_value == 152_000
    detail = await service.get_account(user, acct)
    assert detail.positions[0].symbol == "0050.TW" and detail.positions[0].price == 152.0


async def test_reconcile_never_fills_an_etf_from_missing_data() -> None:
    repo, clock, service, jobs, user, acct = await paper_setup(at(FRI, 10, 0))
    # An ETF nobody subscribed to: no minute bars, no live quote, only the daily rows.
    repo.quotes_latest.pop("0050.TW", None)
    quiet = await service.place_order(user, acct, req("0050.TW", kind="limit", limit="140"))
    assert quiet.status == "working"
    stats = await jobs.reconcile(repo, at(FRI, 14, 41))
    assert stats.matched == 0  # no bars yet and not final: nothing happens
    stats = await jobs.reconcile(repo, at(FRI, 16, 1))
    assert stats.matched == 0 and stats.expired == 1  # final: expires unfilled
    assert (await service.list_orders(user, acct)).orders[0].status == "expired"


async def test_reconcile_fills_an_etf_from_its_official_minute_bars() -> None:
    repo, _, service, jobs, user, acct = await paper_setup(at(FRI, 10, 0))
    repo.quotes_latest["0050.TW"] = quote("0050.TW", 152, at(FRI, 9, 59), reference=150)
    await service.place_order(user, acct, req("0050.TW", kind="limit", limit="150"))
    repo.minute_bars["0050.TW"] = [
        bar("0050.TW", at(FRI, 11, 30), 151, 151, 149.9, 150, official=True),
        bar("0050.TW", at(FRI, 13, 24), 151, 151, 150.5, 151, official=True),
    ]
    stats = await jobs.reconcile(repo, at(FRI, 14, 41))
    assert stats.matched == 1
    fill = (await service.list_fills(user, acct)).fills[0]
    assert fill.price_source == "minute_bar" and fill.price == 150


# ----------------------------------------------------------------- search and overview


async def search_repo() -> MemoryQuantRepository:
    repo = MemoryQuantRepository()
    await repo.upsert_market_stock_days(
        [
            stock_row("2330.TW", FRI, close=1000.0, name="台積電"),
            stock_row("5007.TW", FRI, close=30.0, name="三星"),
        ]
    )
    await repo.upsert_etf_days(
        [
            etf_row("0050.TW", FRI, close=92.4, name="元大台灣50", change=-0.45),
            etf_row("00878.TW", FRI, close=29.26, name="國泰永續高股息"),
            etf_row("0056.TW", FRI, close=46.97, name="元大高股息"),
            etf_row("00679B.TWO", THU, close=26.5, name="元大美債20年"),  # no trade on FRI
        ]
    )
    return repo


async def test_search_finds_etfs_by_code_and_name_with_a_kind() -> None:
    service = InstrumentSearchService(await search_repo())
    by_code = await service.search("0050")
    assert [(i.symbol, i.kind) for i in by_code] == [("0050.TW", "etf")]
    assert [(i.symbol, i.kind) for i in await service.search("00878")] == [("00878.TW", "etf")]
    high = await service.search("高股息")
    assert {i.symbol for i in high} == {"00878.TW", "0056.TW"} and all(
        i.kind == "etf" for i in high
    )
    assert [i.symbol for i in await service.search("00679B")] == ["00679B.TWO"]
    stock = await service.search("2330")
    assert [(i.symbol, i.kind) for i in stock] == [("2330.TW", "stock")]


async def test_a_stock_ranks_before_an_etf_with_the_same_match_quality() -> None:
    repo = await search_repo()
    await repo.upsert_market_stock_days([stock_row("5008.TW", FRI, close=30.0, name="元大高息")])
    found = await InstrumentSearchService(repo).search("元大")
    assert [i.kind for i in found] == ["stock", "etf", "etf", "etf"]


async def test_overview_of_an_etf_comes_from_etf_daily() -> None:
    repo = await search_repo()
    overview = await StockOverviewService(repo).get("0050")
    assert (overview.symbol, overview.kind, overview.name) == ("0050.TW", "etf", "元大台灣50")
    assert overview.full_analysis == "insufficient" and overview.summary.last_close == 92.4
    assert overview.days[-1].foreign_net_lots is None and overview.days[-1].margin_balance is None
    assert not repo.sync_jobs  # no history sync is queued for an ETF
    assert (await StockOverviewService(repo).get("2330.TW")).kind == "stock"
    with pytest.raises(StockNotFound):
        await StockOverviewService(repo).get("00999")


# ------------------------------------------------------------------------ dividends


def test_etf_distributions_are_kept_and_etns_are_not() -> None:
    at_ = datetime(2026, 11, 28, tzinfo=UTC)
    twse = {
        e.symbol: e
        for e in short_src.parse_twse_dividends(load("twse_twt48u_etf.json"), available_at=at_)
    }
    assert twse["00939.TW"].cash_dividend_per_share == pytest.approx(0.15)
    assert twse["00939.TW"].ex_date == date(2026, 12, 1) and twse["00939.TW"].kind == "ex_dividend"
    assert twse["009817.TW"].cash_dividend_per_share == pytest.approx(0.33)
    assert twse["00400A.TW"].cash_dividend_per_share is None  # 待公告實際收益分配金額
    assert "020038.TW" not in twse and "01010T.TW" not in twse  # ETN / 受益證券
    assert any(s[0] in "123456789" for s in twse)  # ordinary stocks still parse
    tpex = {
        e.symbol: e
        for e in short_src.parse_tpex_dividends(
            load("tpex_exright_prepost_etf.json"), available_at=at_
        )
    }
    assert tpex["00679B.TWO"].cash_dividend_per_share == pytest.approx(0.28)
    assert tpex["006201.TWO"].cash_dividend_per_share == pytest.approx(0.9)
    assert tpex["00986B.TWO"].cash_dividend_per_share is None  # 尚未公告
    assert any(s[0] in "123456789" for s in tpex)


# --------------------------------------------------------------------------- HTTP


def test_http_etf_page_and_stock_only_endpoints() -> None:
    import asyncio

    from fastapi.testclient import TestClient

    from quant_signal.adapters.rest.main import app, get_repository
    from quant_signal.adapters.rest.security import Principal, require_principal
    from quant_signal.domain.models import User

    repo = asyncio.run(search_repo())
    user = User(id=new_user(), google_sub="g", email="a@b.c", created_at=datetime.now(UTC))

    async def override():
        yield repo

    app.dependency_overrides[get_repository] = override
    app.dependency_overrides[require_principal] = lambda: Principal("user", user=user)
    try:
        with TestClient(app) as client:
            overview = client.get("/api/v1/stocks/0050.TW/overview")
            assert overview.status_code == 200 and overview.json()["kind"] == "etf"
            assert client.get("/api/v1/stocks/2330/overview").json()["kind"] == "stock"
            found = client.get("/api/v1/instruments/search", params={"q": "00878"}).json()
            assert [(i["symbol"], i["kind"]) for i in found] == [("00878.TW", "etf")]
            # stock-only datasets answer empty, never an error
            for path in (
                "fundamentals",
                "events",
                "holders",
                "short-interest",
                "futures",
                "factors",
            ):
                assert client.get(f"/api/v1/stocks/0050.TW/{path}").status_code == 200, path
            signal = client.get("/api/v1/signals/0050.TW")
            assert signal.status_code == 422 and "ETF 不適用" in signal.json()["detail"]
    finally:
        app.dependency_overrides.clear()
