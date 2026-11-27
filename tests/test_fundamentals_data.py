from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.adapters.rest.security import Principal, require_principal
from quant_signal.application import fundamentals_data as sched
from quant_signal.application.fundamentals_queries import StockFundamentalsService
from quant_signal.application.stocks import StockNotFound
from quant_signal.domain.fundamentals import QuarterlyFinancial, ValuationDay
from quant_signal.domain.models import MarketStockDay, MonthlyRevenue
from quant_signal.infrastructure.db.models import Base
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.infrastructure.providers import fundamentals as fnd
from quant_signal.infrastructure.providers import valuation as val
from quant_signal.quant.quarterly import earnings_metrics, quarter_points, single_quarters
from quant_signal.quant.revenue_trend import revenue_trend
from quant_signal.quant.valuation import percentile_rank, valuation_metrics, weekly_sample

FIXTURES = Path(__file__).parent / "fixtures" / "fundamentals"
TAIPEI = ZoneInfo("Asia/Taipei")
FETCHED = datetime(2026, 11, 28, 2, 0, tzinfo=UTC)
FAR = datetime(2027, 6, 1, tzinfo=UTC)


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- valuation parsers ---------------------------------------------------------------------


def test_twse_valuation_parser_handles_loss_makers_and_units() -> None:
    rows = {
        r.symbol: r
        for r in val.parse_twse_valuation(load("twse_bwibbu.json"), available_at=FETCHED)
    }
    assert set(rows) == {"1101.TW", "1102.TW", "1213.TW", "1218.TW", "2317.TW", "2330.TW"}
    cement = rows["1101.TW"]
    assert cement.trading_date == date(2026, 11, 27)
    assert (cement.pe_ratio, cement.pb_ratio, cement.dividend_yield_pct) == (71.57, 0.80, 3.19)
    assert cement.dividend_year == 2025 and cement.fiscal_period == "2026Q3"
    assert cement.source == "twse_bwibbu" and cement.available_at == FETCHED
    loss = rows["1213.TW"]  # P/E printed as "-"; a 0.00 yield is a real zero (no dividend)
    assert loss.pe_ratio is None and loss.dividend_yield_pct == 0.0 and loss.pb_ratio == 0.64


def test_twse_valuation_holiday_and_bad_payloads_yield_nothing() -> None:
    assert val.parse_twse_valuation(load("twse_bwibbu_holiday.json"), available_at=FETCHED) == []
    assert val.parse_twse_valuation([], available_at=FETCHED) == []
    with pytest.raises(ValueError):
        val.parse_twse_valuation(
            {"stat": "OK", "date": "20261127", "fields": ["x"], "data": [[1]]}, available_at=FETCHED
        )


def test_twse_valuation_drops_non_stock_codes_and_short_rows() -> None:
    payload = load("twse_bwibbu.json")
    payload["data"] += [
        ["00878", "國泰永續高股息", "20.00", "5.00", 114, "-", "1.00", "115/3"],
        ["0050", "元大台灣50", "60.00", "3.00", 114, "-", "1.00", "115/3"],
        ["9999"],
    ]
    rows = val.parse_twse_valuation(payload, available_at=FETCHED)
    assert len(rows) == 6


def test_tpex_valuation_parser_pads_and_na() -> None:
    rows = {
        r.symbol: r for r in val.parse_tpex_valuation(load("tpex_pera.json"), available_at=FETCHED)
    }
    assert set(rows) == {"1240.TWO", "1259.TWO", "1569.TWO", "1595.TWO", "1742.TWO", "6488.TWO"}
    assert rows["1240.TWO"].pe_ratio == 11.20 and rows["1240.TWO"].dividend_yield_pct == 0.97
    assert rows["1240.TWO"].fiscal_period == "2026Q3" and rows["1240.TWO"].dividend_year == 2025
    assert rows["1569.TWO"].pe_ratio is None and rows["1569.TWO"].dividend_yield_pct == 0.0
    assert rows["6488.TWO"].trading_date == date(2026, 11, 27)
    assert val.parse_tpex_valuation(load("tpex_pera_holiday.json"), available_at=FETCHED) == []


def test_fiscal_period_and_params() -> None:
    assert val.parse_fiscal_period("115/3") == "2026Q3"
    assert val.parse_fiscal_period("115Q3") == "2026Q3"
    assert val.parse_fiscal_period("114/4") == "2025Q4"
    assert val.parse_fiscal_period("") is None and val.parse_fiscal_period("115/5") is None
    assert val.twse_valuation_params(date(2026, 11, 27))["date"] == "20261127"
    assert val.tpex_valuation_params(date(2026, 11, 27))["d"] == "115/11/27"


# --- revenue history parser ----------------------------------------------------------------


def revenue_page(name: str) -> str:
    return fnd.decode_mops_revenue((FIXTURES / name).read_bytes())


def test_mops_revenue_parser_skips_subtotals_and_keeps_units() -> None:
    rows = {
        r.symbol: r
        for r in fnd.parse_mops_revenue_html(
            revenue_page("mops_revenue_sii.html"),
            period="2026-10",
            suffix=".TW",
            available_at=fnd.revenue_deadline("2026-10"),
        )
    }
    assert set(rows) == {"1101.TW", "1102.TW", "1201.TW", "1301.TW"}  # 合計 rows dropped
    cement = rows["1101.TW"]
    assert cement.revenue == 16_357_176.0 and cement.mom_pct == 14.21 and cement.yoy_pct == 17.82
    assert cement.cumulative_revenue == 129_405_701.0 and cement.cumulative_yoy_pct == 4.93
    assert rows["1102.TW"].yoy_pct == -6.90
    assert cement.source == "mops_revenue_history"
    assert cement.available_at == datetime(2026, 11, 10, 23, 59, tzinfo=TAIPEI)
    otc = fnd.parse_mops_revenue_html(
        revenue_page("mops_revenue_otc.html"),
        period="2026-10",
        suffix=".TWO",
        available_at=FETCHED,
    )
    assert otc and all(r.symbol.endswith(".TWO") and r.period == "2026-10" for r in otc)


def test_statutory_deadlines() -> None:
    assert fnd.revenue_deadline("2026-10") == datetime(2026, 11, 10, 23, 59, tzinfo=TAIPEI)
    assert fnd.revenue_deadline("2026-12") == datetime(2027, 1, 10, 23, 59, tzinfo=TAIPEI)
    assert fnd.financials_deadline(2026, 1).date() == date(2026, 5, 15)
    assert fnd.financials_deadline(2026, 2).date() == date(2026, 8, 14)
    assert fnd.financials_deadline(2026, 3).date() == date(2026, 11, 14)
    assert fnd.financials_deadline(2026, 4).date() == date(2027, 3, 31)
    assert fnd.mops_revenue_url("sii", "2026-10").endswith("/sii/t21sc03_115_10_0.html")
    assert fnd.mops_statement_form("otc", 2026, 3)["season"] == "03"


# --- statement parsers ---------------------------------------------------------------------


def forward_rows(market: str = "twse", kind: str = "ci") -> list[QuarterlyFinancial]:
    suffix = ".TW" if market == "twse" else ".TWO"
    return fnd.build_quarterly_financials(
        load(f"{market}_income_{kind}.json"),
        load(f"{market}_balance_{kind}.json"),
        suffix=suffix,
        source=f"{market}_financials",
        available_at=FETCHED,
    )


def test_openapi_general_statements_join_income_and_balance() -> None:
    rows = {r.symbol: r for r in forward_rows()}
    cement = rows["1101.TW"]
    assert (cement.fiscal_year, cement.fiscal_quarter) == (2026, 3)
    assert cement.statement_kind == "general" and cement.cumulative is True
    assert cement.revenue == 112_522_875.0 and cement.gross_profit == 21_229_246.0
    assert cement.operating_income == 9_571_599.0
    assert cement.net_income_parent == 5_089_876.0 and cement.eps == 0.63
    assert cement.equity_parent == 240_317_636.0 and cement.total_assets == 602_031_626.0
    # owners' profit blank and no minority interest: the whole net profit is theirs
    small = rows["1342.TW"]
    assert small.net_income_parent == 443_777.0 and small.eps == 5.70
    assert small.equity_parent is not None and small.total_assets == 3_929_831.0
    assert cement.available_at == FETCHED


def test_openapi_tpex_and_financial_sector() -> None:
    tpex = {r.symbol: r for r in forward_rows("tpex")}
    assert tpex["1240.TWO"].revenue == 2_217_728.0 and tpex["1240.TWO"].eps == 3.63
    assert tpex["1777.TWO"].net_income_parent == 276_804.0
    banks = {r.symbol: r for r in forward_rows("twse", "basi")}
    bank = banks["2801.TW"]
    assert bank.statement_kind == "financial"
    assert bank.revenue is None and bank.gross_profit is None and bank.operating_income is None
    assert bank.net_income_parent == 17_370_040.0 and bank.eps == 1.44
    assert bank.equity_parent == 231_224_375.0 and bank.total_assets == 3_578_823_619.0
    # an empty placeholder table (no company codes) builds nothing
    assert (
        fnd.build_quarterly_financials(
            load("tpex_income_mim_empty.json"), [], suffix=".TWO", source="x"
        )
        == []
    )


def test_mops_statement_html_parses_all_tables_and_uses_requested_period() -> None:
    income = fnd.parse_mops_statement_html(text("mops_income.html"))
    balance = fnd.parse_mops_statement_html(text("mops_balance.html"))
    assert {row["公司代號"] for row in income} == {"1101", "2330", "2801", "2880"}
    rows = {
        r.symbol: r
        for r in fnd.build_quarterly_financials(
            income, balance, suffix=".TW", source=fnd.MOPS_FINANCIALS_SOURCE, fiscal=(2025, 3)
        )
    }
    cement = rows["1101.TW"]
    assert cement.statement_kind == "general" and cement.revenue == 109_377_581.0
    assert cement.net_income_parent == -9_239_402.0 and cement.eps == -1.28
    assert cement.equity_parent == 223_471_065.0 and cement.total_assets == 581_076_431.0
    assert cement.available_at == datetime(2025, 11, 14, 23, 59, tzinfo=TAIPEI)
    holding = rows["2880.TW"]
    assert holding.statement_kind == "financial" and holding.revenue is None
    assert holding.net_income_parent == 19_909_736.0 and holding.eps == 1.43
    assert holding.equity_parent == 227_872_286.0


def test_mops_statement_page_without_data_or_garbage() -> None:
    assert fnd.parse_mops_statement_html(text("mops_no_data.html")) == []
    with pytest.raises(ValueError):
        fnd.parse_mops_statement_html(
            "<html>FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED</html>"
        )


# --- percentile and weekly sampling --------------------------------------------------------


def day_row(
    symbol: str,
    day: date,
    pe: float | None,
    *,
    pb: float | None = 1.0,
    y: float | None = 2.0,
    at: datetime | None = None,
) -> ValuationDay:
    return ValuationDay(
        symbol=symbol,
        trading_date=day,
        source="twse_bwibbu",
        pe_ratio=pe,
        pb_ratio=pb,
        dividend_yield_pct=y,
        dividend_year=2025,
        fiscal_period="2026Q3",
        available_at=at or datetime.combine(day, datetime.min.time(), TAIPEI) + timedelta(hours=18),
    )


def weekdays(count: int, end: date = date(2026, 11, 27)) -> list[date]:
    days: list[date] = []
    current = end
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return sorted(days)


def test_percentile_rank_definition() -> None:
    window = [float(n) for n in range(1, 101)]
    assert percentile_rank(window, 100.0) == 100.0
    assert percentile_rank(window, 50.0) == 50.0
    assert percentile_rank(window, 1.0) == 1.0
    assert percentile_rank(window[:59], 59.0) is None  # fewer than 60 observations
    assert percentile_rank([None] * 70, 5.0) is None and percentile_rank(window, None) is None


def test_valuation_metrics_uses_own_newest_250_and_ignores_missing() -> None:
    days = weekdays(300)
    rows = [day_row("2330.TW", d, float(i + 1)) for i, d in enumerate(days)]  # rising P/E
    metrics = valuation_metrics(rows)
    assert metrics is not None and metrics.trading_date == days[-1] and metrics.pe_ratio == 300.0
    assert metrics.pe_percentile_250d == 100.0 and metrics.pe_observations == 250
    # P/E falls to the window minimum: only itself is <=, 1/250
    low = rows[:-1] + [day_row("2330.TW", days[-1], 0.5)]
    assert valuation_metrics(low).pe_percentile_250d == pytest.approx(0.4)
    # loss-making today: no percentile; thin history: no percentile
    assert (
        valuation_metrics(rows[:-1] + [day_row("2330.TW", days[-1], None)]).pe_percentile_250d
        is None
    )
    assert valuation_metrics(rows[-59:]).pe_percentile_250d is None
    assert valuation_metrics([]) is None


def test_weekly_sample_keeps_last_day_of_each_week() -> None:
    rows = [day_row("2330.TW", d, 10.0) for d in weekdays(30)]
    sample = weekly_sample(rows, limit=60)
    assert len(sample) == 6 and all(item.trading_date.weekday() == 4 for item in sample[:-1])
    assert len(weekly_sample(rows, limit=3)) == 3


# --- revenue trend -------------------------------------------------------------------------


def revenue_row(period: str, revenue: float, **extra) -> MonthlyRevenue:
    return MonthlyRevenue(
        symbol="2330.TW",
        period=period,
        revenue=revenue,
        source="twse_revenue",
        available_at=FETCHED,
        **extra,
    )


def month_periods(start: str, count: int) -> list[str]:
    year, month = (int(p) for p in start.split("-"))
    out = []
    for _ in range(count):
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def test_revenue_trend_rolling_windows_streak_and_highs() -> None:
    periods = month_periods("2024-01", 25)  # revenue 100..124
    rows = [revenue_row(p, 100.0 + i) for i, p in enumerate(periods)]
    trend = revenue_trend(rows)
    assert trend is not None and trend.latest_period == "2026-01" and trend.history_months == 25
    assert trend.yoy_3m_pct == pytest.approx(369 / 333 * 100 - 100, abs=0.01)
    assert trend.yoy_12m_pct == pytest.approx(1422 / 1278 * 100 - 100, abs=0.01)
    assert trend.is_12m_high is True and trend.is_history_high is True
    assert trend.growth_streak_months == 13  # 2025-01 .. 2026-01 have a year-earlier twin
    last = trend.months[-1]
    assert last.yoy_pct == pytest.approx(124 / 112 * 100 - 100, abs=0.01)  # derived: none published
    assert last.mom_pct == pytest.approx(124 / 123 * 100 - 100, abs=0.01)
    assert trend.months[0].yoy_pct is None
    assert revenue_trend(rows, include_months=False).months == []


def test_revenue_trend_gaps_prefer_published_figures_and_latest_revision() -> None:
    rows = [
        revenue_row("2026-08", 100.0, yoy_pct=5.0),
        revenue_row("2026-09", 90.0, yoy_pct=-3.0),
        revenue_row("2026-10", 80.0, yoy_pct=2.0),
        revenue_row("2026-10", 85.0, yoy_pct=4.0, revision=2),
    ]
    trend = revenue_trend(rows)
    assert trend.months[-1].revenue == 85.0 and trend.months[-1].yoy_pct == 4.0
    assert trend.yoy_3m_pct is None and trend.yoy_12m_pct is None  # no year-earlier twins
    assert trend.growth_streak_months == 1  # September's -3% ends the streak
    assert trend.is_12m_high is None and trend.is_history_high is None
    assert revenue_trend([]) is None


# --- quarterly derivation ------------------------------------------------------------------


def cumulative(
    year: int, quarter: int, revenue, gross, operating, net, eps, equity=None, **extra
) -> QuarterlyFinancial:
    return QuarterlyFinancial(
        symbol="2330.TW",
        fiscal_year=year,
        fiscal_quarter=quarter,
        revenue=revenue,
        gross_profit=gross,
        operating_income=operating,
        net_income_parent=net,
        eps=eps,
        equity_parent=equity,
        total_assets=None,
        source="twse_financials",
        available_at=FETCHED,
        **extra,
    )


STATEMENTS = [
    cumulative(2025, 1, 100, 30, 10, 8, 0.8),
    cumulative(2025, 2, 210, 62, 22, 17, 1.7),
    cumulative(2025, 3, 330, 100, 36, 27, 2.7),
    cumulative(2025, 4, 460, 140, 50, 38, 3.8),
    cumulative(2026, 1, 120, 42, 15, 12, 1.2),
    cumulative(2026, 2, 250, 90, 33, 26, 2.6, equity=200),
]


def test_single_quarters_are_differences_of_cumulative_rows() -> None:
    singles = {s.period: s for s in single_quarters(STATEMENTS)}
    assert singles["2025Q1"].revenue == 100  # Q1 stands as is
    assert singles["2025Q2"].revenue == 110 and singles["2025Q2"].gross_profit == 32
    assert singles["2025Q4"].revenue == 130 and singles["2025Q4"].eps == pytest.approx(1.1)
    assert singles["2026Q2"].net_income_parent == 14 and singles["2026Q2"].eps == pytest.approx(1.4)
    # a missing predecessor makes the quarter underivable, never a guess
    gappy = [r for r in STATEMENTS if (r.fiscal_year, r.fiscal_quarter) != (2026, 1)]
    assert {s.period: s for s in single_quarters(gappy)}["2026Q2"].revenue is None


def test_earnings_metrics_ttm_margins_and_roe() -> None:
    metrics = earnings_metrics(STATEMENTS)
    assert metrics is not None and metrics.period == "2026Q2"
    assert metrics.eps == pytest.approx(1.4) and metrics.eps_yoy_pct == pytest.approx(55.56)
    assert metrics.eps_ttm == pytest.approx(2.6 + 3.8 - 1.7)  # YTD + last FY - same YTD a year ago
    assert metrics.gross_margin_pct == pytest.approx(
        36.92
    ) and metrics.gross_margin_yoy_pp == pytest.approx(7.83)
    assert metrics.operating_margin_pct == pytest.approx(13.85)
    assert metrics.net_margin_pct == pytest.approx(10.77)
    assert metrics.net_margin_yoy_pp == pytest.approx(10.77 - 8.18, abs=0.01)
    assert metrics.net_income_ttm == 26 + 38 - 17
    assert metrics.roe_ttm_pct == pytest.approx(47 / 200 * 100)
    points = quarter_points(STATEMENTS, 3)
    assert [p.period for p in points] == ["2025Q4", "2026Q1", "2026Q2"]
    assert points[-1].gross_margin_pct == pytest.approx(36.92)


def test_earnings_metrics_edge_cases() -> None:
    assert earnings_metrics([]) is None
    q4 = earnings_metrics(STATEMENTS[:4])  # the annual figure is the TTM
    assert q4.period == "2025Q4" and q4.eps_ttm == pytest.approx(3.8) and q4.roe_ttm_pct is None
    assert q4.eps_yoy_pct is None  # no prior-year quarter yet
    # a loss a year ago makes EPS growth meaningless
    loss = [cumulative(2025, 1, 100, 30, 10, -8, -0.8), cumulative(2026, 1, 120, 42, 15, 12, 1.2)]
    assert earnings_metrics(loss).eps_yoy_pct is None
    # a restated quarter wins when it is the higher revision
    restated = [*STATEMENTS, cumulative(2026, 2, 250, 90, 33, 30, 3.0, equity=200, revision=2)]
    assert earnings_metrics(restated).eps == pytest.approx(1.8)
    # financial sector: no revenue-based ratios, EPS and ROE still work
    bank = [
        cumulative(2025, 4, None, None, None, 100, 4.0, statement_kind="financial"),
        cumulative(2026, 1, None, None, None, 30, 1.0, equity=1000, statement_kind="financial"),
    ]
    bank_metrics = earnings_metrics(bank)
    assert bank_metrics.gross_margin_pct is None and bank_metrics.net_margin_pct is None
    assert bank_metrics.eps == 1.0 and bank_metrics.eps_ttm is None  # needs the Q1 of last year


# --- scheduler -----------------------------------------------------------------------------


class FakeExchanges:
    """Serves the recorded fixtures for every endpoint the fundamentals scheduler calls."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.requests: Counter[str] = Counter()
        self.valuation_days: list[str] = []
        self.fail = fail or set()
        self.eps_override: str | None = None
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        key = f"{url.host}{url.path}"
        self.requests[key] += 1
        if any(key.startswith(prefix) for prefix in self.fail):
            return httpx.Response(500)
        if key.endswith("BWIBBU_d"):
            requested = url.params["date"]
            self.valuation_days.append(requested)
            payload = load("twse_bwibbu.json")
            payload["date"] = requested
            return httpx.Response(200, json=payload)
        if key.endswith("pera_result.php"):
            payload = load("tpex_pera.json")
            payload["tables"][0]["date"] = url.params["d"]
            return httpx.Response(200, json=payload)
        if url.host == "mopsov.twse.com.tw" and "/nas/t21/" in key:
            name = "mops_revenue_sii.html" if "/sii/" in key else "mops_revenue_otc.html"
            return httpx.Response(200, content=(FIXTURES / name).read_bytes())
        if url.host == "mopsov.twse.com.tw":
            form = dict(item.split("=") for item in request.content.decode().split("&"))
            if form["TYPEK"] == "otc":
                return httpx.Response(200, text=text("mops_no_data.html"))
            name = "mops_income.html" if key.endswith("sb04") else "mops_balance.html"
            return httpx.Response(200, text=text(name))
        if "t187ap06" in key or "t187ap07" in key:
            market = "twse" if url.host == "openapi.twse.com.tw" else "tpex"
            kind = key.rsplit("_", 1)[-1]
            side = "income" if "t187ap06" in key else "balance"
            if kind not in ("ci", "basi"):
                return httpx.Response(200, json=[])
            if market == "tpex" and kind == "basi":
                return httpx.Response(200, json=[])
            payload = load(f"{market}_{side}_{kind}.json")
            if side == "income" and self.eps_override and market == "twse" and kind == "ci":
                for row in payload:
                    row["基本每股盈餘（元）"] = self.eps_override
            return httpx.Response(200, json=payload)
        raise AssertionError(f"unexpected request {request.url}")

    def count(self, fragment: str) -> int:
        return sum(n for key, n in self.requests.items() if fragment in key)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(sched, "MIN_VALUATION_ROWS", 1)
    monkeypatch.setattr(sched, "MIN_REVENUE_ROWS", 1)


def taipei(day: int, hour: int, minute: int = 0, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=TAIPEI).astimezone(UTC)


def stock_day(day: date) -> MarketStockDay:
    return MarketStockDay(
        symbol="2330.TW",
        name="台積電",
        market="TW",
        trading_date=day,
        open=1,
        high=1,
        low=1,
        close=1,
        volume=1,
        turnover_value=1,
        source="twse_official",
        available_at=FETCHED,
    )


def test_calendar_helpers() -> None:
    assert sched.trading_session_open(taipei(27, 10)) and not sched.trading_session_open(
        taipei(27, 16)
    )
    assert not sched.trading_session_open(taipei(28, 10))  # Saturday
    assert sched.financials_slot(taipei(28, 9)) == "2026-11-28"  # filing month: daily
    assert sched.financials_slot(taipei(28, 9, month=12)).startswith("2026-W")
    assert sched.closed_revenue_months(taipei(28, 9), 3) == ["2026-10", "2026-09", "2026-08"]
    # before the 10th the previous month's deadline has not passed
    assert sched.closed_revenue_months(taipei(5, 9, month=11), 1) == ["2026-09"]
    assert sched.closed_quarters(taipei(28, 9), 3) == [(2026, 3), (2026, 2), (2026, 1)]
    assert sched.closed_quarters(taipei(20, 9, month=2), 2) == [(2025, 3), (2025, 2)]
    assert sched.closed_quarters(taipei(1, 9, month=4), 2) == [(2025, 4), (2025, 3)]


async def test_valuation_forward_fetch_is_idempotent_and_stamps_fetch_time() -> None:
    repo, exchange = MemoryQuantRepository(), FakeExchanges()
    moment = taipei(27, 18)
    written = await sched.refresh_fundamentals_data(
        repo,
        exchange,
        exchange,
        now=moment,
        attempts={},
        request_interval=0,
        revenue_history_enabled=False,
        financials_enabled=False,
        valuation_window=2,
    )
    assert written[sched.VALUATION_DAILY] == 2 * 12  # 6 TWSE + 6 TPEx rows on each of 2 days
    assert exchange.valuation_days == ["20261126", "20261127"]
    history = await repo.valuation_history("1101.TW", moment)
    assert [r.trading_date for r in history] == [date(2026, 11, 26), date(2026, 11, 27)]
    assert all(r.available_at == moment for r in history)
    again = await sched.refresh_fundamentals_data(
        repo,
        exchange,
        exchange,
        now=moment,
        attempts={},
        request_interval=0,
        revenue_history_enabled=False,
        financials_enabled=False,
        valuation_window=2,
    )
    assert again == {} and exchange.valuation_days == ["20261126", "20261127"]  # no refetch


async def test_valuation_today_not_published_yet_is_retried_later() -> None:
    repo, exchange = MemoryQuantRepository(), FakeExchanges()

    class Empty(FakeExchanges):
        def _handle(self, request):
            if request.url.path.endswith(("BWIBBU_d", "pera_result.php")):
                return httpx.Response(200, json={"stat": "no data", "total": 0})
            return super()._handle(request)

    empty, attempts = Empty(), {}
    await sched.refresh_fundamentals_data(
        repo,
        empty,
        empty,
        now=taipei(27, 17, 5),
        attempts=attempts,
        request_interval=0,
        revenue_history_enabled=False,
        financials_enabled=False,
        valuation_window=1,
    )
    assert sched.VALUATION_DAILY in attempts
    assert not await repo.list_fetch_coverage(
        sched.SOURCE, sched.VALUATION_DAILY, "*", ["2026-11-27"]
    )
    before = empty.count("BWIBBU_d")
    await sched.refresh_fundamentals_data(  # inside the retry delay: nothing is requested
        repo,
        empty,
        empty,
        now=taipei(27, 17, 7),
        attempts=attempts,
        request_interval=0,
        revenue_history_enabled=False,
        financials_enabled=False,
        valuation_window=1,
    )
    assert empty.count("BWIBBU_d") == before
    await sched.refresh_fundamentals_data(
        repo,
        exchange,
        exchange,
        now=taipei(27, 17, 30),
        attempts=attempts,
        request_interval=0,
        revenue_history_enabled=False,
        financials_enabled=False,
        valuation_window=1,
    )
    assert (await repo.latest_valuation_metrics(taipei(27, 18)))["1101.TW"].pe_ratio == 71.57


async def test_valuation_exchange_failure_never_raises_and_is_remembered() -> None:
    repo, exchange, attempts = MemoryQuantRepository(), FakeExchanges({"www.tpex.org.tw"}), {}
    written = await sched.refresh_fundamentals_data(
        repo,
        exchange,
        exchange,
        now=taipei(27, 18),
        attempts=attempts,
        request_interval=0,
        revenue_history_enabled=False,
        financials_enabled=False,
        valuation_window=1,
        fetch_attempts=1,
    )
    assert sched.VALUATION_DAILY in attempts and written[sched.VALUATION_DAILY] == 6  # TWSE kept
    assert not await repo.list_fetch_coverage(
        sched.SOURCE, sched.VALUATION_DAILY, "*", ["2026-11-27"]
    )


async def test_valuation_backfill_is_gradual_newest_first_resumable_and_off_session() -> None:
    repo, exchange = MemoryQuantRepository(), FakeExchanges()
    sessions = [date(2026, 11, 4), date(2026, 11, 3), date(2026, 11, 2), date(2026, 10, 30)]
    await repo.upsert_market_stock_days([stock_day(day) for day in sessions])
    common = dict(
        request_interval=0,
        revenue_history_enabled=False,
        financials_enabled=False,
        valuation_window=1,
        valuation_backfill_sessions=3,
        backfill_per_tick=2,
    )
    attempts: dict = {}
    saturday = taipei(28, 9)
    await sched.refresh_fundamentals_data(
        repo, exchange, exchange, now=saturday, attempts=attempts, **common
    )
    assert exchange.valuation_days == ["20261127"]  # the live window comes first
    exchange.valuation_days.clear()
    monday_morning = taipei(30, 10)  # during the trading session: no backfill
    await sched.refresh_fundamentals_data(
        repo, exchange, exchange, now=monday_morning, attempts=attempts, **common
    )
    assert exchange.valuation_days == []  # window covered, backfill waits for the evening
    exchange.valuation_days.clear()
    first = await sched.refresh_fundamentals_data(
        repo, exchange, exchange, now=saturday, attempts=attempts, **common
    )
    assert first[sched.VALUATION_DAILY] == 2 * 12 and exchange.valuation_days == [
        "20261104",
        "20261103",
    ]
    second = await sched.refresh_fundamentals_data(
        repo, exchange, exchange, now=saturday, attempts=attempts, **common
    )
    assert second[sched.VALUATION_DAILY] == 12 and exchange.valuation_days[-1] == "20261102"
    # only the newest three sessions were wanted; the fourth is never fetched
    assert (
        await sched.refresh_fundamentals_data(
            repo, exchange, exchange, now=saturday, attempts=attempts, **common
        )
        == {}
    )
    assert "20261030" not in exchange.valuation_days
    old = (await repo.valuation_history("1101.TW", FAR))[0]
    assert old.trading_date == date(2026, 11, 2)
    assert old.available_at == datetime(2026, 11, 2, 18, 0, tzinfo=TAIPEI)  # documented close stamp
    # a point-in-time read before that stamp does not see it
    assert (
        await repo.valuation_history("1101.TW", datetime(2026, 11, 2, 17, 0, tzinfo=TAIPEI)) == []
    )


async def test_revenue_history_backfill_adds_only_missing_months_with_deadline_stamp() -> None:
    repo, exchange = MemoryQuantRepository(), FakeExchanges()
    forward = MonthlyRevenue(
        symbol="1101.TW", period="2026-10", revenue=1.0, source="twse_revenue", available_at=FETCHED
    )
    await repo.upsert_monthly_revenue([forward])
    common = dict(
        request_interval=0,
        mops_interval=0,
        valuation_enabled=False,
        financials_enabled=False,
        revenue_backfill_months=3,
        backfill_per_tick=3,
    )
    now = taipei(28, 9)
    written = await sched.refresh_fundamentals_data(repo, exchange, exchange, now=now, **common)
    assert exchange.count("/nas/t21/") == 4  # 2 months per tick x 2 markets
    assert written[sched.REVENUE_HISTORY] > 0
    rows = await repo.monthly_revenue("1101.TW", FAR, 36)
    by_period = {r.period: r for r in rows}
    assert by_period["2026-10"].source == "twse_revenue" and by_period["2026-10"].revenue == 1.0
    assert by_period["2026-09"].source == "mops_revenue_history"
    assert by_period["2026-09"].available_at == datetime(2026, 10, 10, 23, 59, tzinfo=TAIPEI)
    # visible only after the statutory deadline
    assert (
        await repo.monthly_revenue("1101.TW", datetime(2026, 10, 10, 12, tzinfo=TAIPEI), 36) == []
    )
    again = await sched.refresh_fundamentals_data(repo, exchange, exchange, now=now, **common)
    assert again[sched.REVENUE_HISTORY] >= 0 and sched.REVENUE_HISTORY in again
    assert (
        await sched.refresh_fundamentals_data(repo, exchange, exchange, now=now, **common)
    ) == {}
    during = await sched.refresh_fundamentals_data(
        MemoryQuantRepository(), exchange, exchange, now=taipei(30, 10), **common
    )
    assert during == {}


async def test_financials_forward_ingest_revisions_and_slots() -> None:
    repo, exchange, attempts = MemoryQuantRepository(), FakeExchanges(), {}
    common = dict(
        request_interval=0,
        mops_interval=0,
        valuation_enabled=False,
        revenue_history_enabled=False,
        financials_backfill_quarters=0,
        attempts=attempts,
    )
    now = taipei(28, 9)
    written = await sched.refresh_fundamentals_data(repo, exchange, exchange, now=now, **common)
    assert written[sched.FINANCIALS_FORWARD] == 7  # 3 + 2 TWSE, 2 TPEx
    assert exchange.count("t187ap06_L_") == 6 and exchange.count("mopsfin_t187ap07_O_") == 6
    rows = await repo.quarterly_financials("1101.TW", FAR)
    assert len(rows) == 1 and rows[0].available_at == now and rows[0].source == "twse_financials"
    assert (await repo.quarterly_financials("2801.TW", FAR))[0].statement_kind == "financial"
    # same slot: nothing; next day with identical figures: no new revision
    before = exchange.count("t187ap06_L_ci")
    assert await sched.refresh_fundamentals_data(repo, exchange, exchange, now=now, **common) == {}
    assert exchange.count("t187ap06_L_ci") == before
    nextday = await sched.refresh_fundamentals_data(
        repo, exchange, exchange, now=taipei(29, 9), **common
    )
    assert nextday[sched.FINANCIALS_FORWARD] == 0
    # a restated EPS becomes revision 2 visible only from its fetch time
    exchange.eps_override = "0.70"
    restated_at = taipei(30, 20)
    changed = await sched.refresh_fundamentals_data(
        repo, exchange, exchange, now=restated_at, **common
    )
    assert changed[sched.FINANCIALS_FORWARD] == 3  # every TWSE general company changed
    assert (await repo.quarterly_financials("1101.TW", restated_at))[0].eps == 0.70
    assert (await repo.quarterly_financials("1101.TW", taipei(29, 12)))[0].eps == 0.63


async def test_financials_forward_failure_of_required_statement_is_retried() -> None:
    repo, attempts = MemoryQuantRepository(), {}
    broken = FakeExchanges({"openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"})
    common = dict(
        request_interval=0,
        mops_interval=0,
        valuation_enabled=False,
        revenue_history_enabled=False,
        financials_backfill_quarters=0,
        attempts=attempts,
        fetch_attempts=1,
    )
    now = taipei(28, 9)
    await sched.refresh_fundamentals_data(repo, broken, broken, now=now, **common)
    assert sched.FINANCIALS_FORWARD in attempts
    assert not await repo.list_fetch_coverage(
        sched.SOURCE, sched.FINANCIALS_FORWARD, "*", ["2026-11-28"]
    )
    healthy = FakeExchanges()
    await sched.refresh_fundamentals_data(
        repo, healthy, healthy, now=now + timedelta(minutes=11), **common
    )
    assert sched.FINANCIALS_FORWARD not in attempts
    assert await repo.list_fetch_coverage(
        sched.SOURCE, sched.FINANCIALS_FORWARD, "*", ["2026-11-28"]
    )


async def test_financials_history_backfills_one_quarter_per_tick_with_deadline_stamp() -> None:
    repo, exchange = MemoryQuantRepository(), FakeExchanges()
    common = dict(
        request_interval=0,
        mops_interval=0,
        valuation_enabled=False,
        revenue_history_enabled=False,
        financials_backfill_quarters=3,
    )
    now = taipei(28, 9)
    # the forward statements are not what this test is about: hold the latest quarter first
    await repo.upsert_quarterly_financials([r for r in forward_rows() if r.symbol == "1101.TW"])
    attempts: dict = {}
    written = await sched.refresh_fundamentals_data(
        repo, exchange, exchange, now=now, attempts=attempts, **common
    )
    # the forward run (same tick) already holds 2330 and 2801: only 2880 is new from MOPS
    assert written[sched.FINANCIALS_HISTORY] == 1
    assert exchange.count("ajax_t163sb04") == 2 and exchange.count("ajax_t163sb05") == 2
    holding = (await repo.quarterly_financials("2880.TW", FAR))[0]
    assert (holding.fiscal_year, holding.fiscal_quarter) == (2026, 3)
    assert holding.source == "mops_financials"
    assert holding.available_at == datetime(2026, 11, 14, 23, 59, tzinfo=TAIPEI)
    cement = (await repo.quarterly_financials("1101.TW", FAR))[0]
    assert cement.source == "twse_financials"  # published rows are never overwritten
    assert await repo.list_fetch_coverage(sched.SOURCE, sched.FINANCIALS_HISTORY, "*", ["2026Q3"])
    await sched.refresh_fundamentals_data(
        repo, exchange, exchange, now=now, attempts=attempts, **common
    )
    assert await repo.list_fetch_coverage(sched.SOURCE, sched.FINANCIALS_HISTORY, "*", ["2026Q2"])
    assert not await repo.list_fetch_coverage(
        sched.SOURCE, sched.FINANCIALS_HISTORY, "*", ["2026Q1"]
    )


async def test_financials_history_failure_is_remembered_and_never_raises() -> None:
    repo, attempts = MemoryQuantRepository(), {}
    broken = FakeExchanges({"mopsov.twse.com.tw"})
    common = dict(
        request_interval=0,
        mops_interval=0,
        valuation_enabled=False,
        revenue_history_enabled=False,
        financials_backfill_quarters=2,
        fetch_attempts=1,
        attempts=attempts,
    )
    await sched.refresh_fundamentals_data(repo, broken, broken, now=taipei(28, 9), **common)
    assert sched.FINANCIALS_HISTORY in attempts
    assert not await repo.list_fetch_coverage(
        sched.SOURCE, sched.FINANCIALS_HISTORY, "*", ["2026Q3"]
    )


async def test_flags_disable_everything() -> None:
    repo, exchange = MemoryQuantRepository(), FakeExchanges()
    written = await sched.refresh_fundamentals_data(
        repo,
        exchange,
        exchange,
        now=taipei(28, 9),
        valuation_enabled=False,
        revenue_history_enabled=False,
        financials_enabled=False,
    )
    assert written == {} and not exchange.requests


# --- reads, service and API ----------------------------------------------------------------


async def test_whole_market_reads_are_point_in_time_and_keyed_by_symbol() -> None:
    repo = MemoryQuantRepository()
    days = weekdays(70)
    await repo.upsert_valuation_daily(
        [day_row("2330.TW", d, 10.0 + i) for i, d in enumerate(days)]
        + [day_row("2317.TW", d, None, pb=1.5 + i / 100) for i, d in enumerate(days)]
    )
    cutoff = datetime(2026, 11, 28, tzinfo=TAIPEI)
    valuation = await repo.latest_valuation_metrics(cutoff)
    assert set(valuation) == {"2317.TW", "2330.TW"}
    assert (
        valuation["2330.TW"].pe_percentile_250d == 100.0
        and valuation["2330.TW"].pe_observations == 70
    )
    assert (
        valuation["2317.TW"].pe_percentile_250d is None
        and valuation["2317.TW"].pb_percentile_250d == 100.0
    )
    earlier = await repo.latest_valuation_metrics(
        datetime.combine(days[40], datetime.min.time(), TAIPEI) + timedelta(hours=19)
    )
    assert earlier["2330.TW"].trading_date == days[40] and earlier["2330.TW"].pe_observations == 41
    assert set(await repo.latest_valuation_metrics(cutoff, ["2330.TW"])) == {"2330.TW"}
    assert await repo.latest_valuation_metrics(cutoff, []) == {}

    await repo.upsert_quarterly_financials(STATEMENTS)
    earnings = await repo.earnings_summaries(FAR)
    assert earnings["2330.TW"].period == "2026Q2" and earnings[
        "2330.TW"
    ].roe_ttm_pct == pytest.approx(23.5)
    assert await repo.earnings_summaries(datetime(2020, 1, 1, tzinfo=UTC)) == {}

    await repo.upsert_monthly_revenue(
        [revenue_row(p, 100.0 + i) for i, p in enumerate(month_periods("2024-01", 25))]
    )
    trends = await repo.revenue_trends(FAR)
    assert trends["2330.TW"].yoy_3m_pct is not None and trends["2330.TW"].months == []
    assert (await repo.revenue_trends(FAR, include_months=True))["2330.TW"].months


@pytest.fixture
async def api_repo() -> MemoryQuantRepository:
    repo = MemoryQuantRepository()
    days = weekdays(260)
    await repo.upsert_valuation_daily(
        [day_row("2330.TW", d, 10.0 + i / 10) for i, d in enumerate(days)]
    )
    rows = [revenue_row(p, 100.0 + i) for i, p in enumerate(month_periods("2024-01", 25))]
    backfilled = [r.model_copy(update={"source": "mops_revenue_history"}) for r in rows[:12]]
    await repo.upsert_monthly_revenue(backfilled + rows[12:])
    await repo.upsert_quarterly_financials(
        [r.model_copy(update={"source": "mops_financials"}) for r in STATEMENTS[:3]]
        + STATEMENTS[3:]
    )
    return repo


async def test_service_builds_all_sections_with_notes(api_repo) -> None:
    result = await StockFundamentalsService(api_repo).get("2330.TW", as_of=date(2026, 11, 28))
    assert result.symbol == "2330.TW"
    assert result.valuation is not None and result.valuation.date == date(2026, 11, 27)
    assert result.valuation.pe_percentile_250d == 100.0 and 1 < len(result.valuation.history) <= 60
    assert result.valuation.history[0].date < result.valuation.history[-1].date
    assert result.revenue is not None and len(result.revenue.months) == 25
    assert result.revenue.growth_streak_months == 13 and result.revenue.is_12m_high is True
    assert result.earnings is not None and len(result.earnings.quarters) == 6
    assert result.earnings.quarters[-1].period == "2026Q2"
    assert result.earnings.eps_ttm == pytest.approx(
        4.7
    ) and result.earnings.roe_ttm_pct == pytest.approx(23.5)
    joined = "\n".join(result.notes)
    assert "法定申報期限" in joined and "累計" in joined and "百分位" in joined


async def test_service_sections_are_null_without_data_and_unknown_symbol_404s() -> None:
    repo = MemoryQuantRepository()
    result = await StockFundamentalsService(repo).get("2330.TW", as_of=date(2026, 11, 28))
    assert result.valuation is None and result.revenue is None and result.earnings is None
    assert result.notes and "尚無" in result.notes[-1]
    with pytest.raises(StockNotFound):
        await StockFundamentalsService(repo).get("9998", as_of=date(2026, 11, 28))
    early = await StockFundamentalsService(MemoryQuantRepository()).get(
        "2330.TW", as_of=date(2020, 1, 1)
    )
    assert early.valuation is None


def test_fundamentals_endpoint(api_repo) -> None:
    app.dependency_overrides[get_repository] = lambda: api_repo
    app.dependency_overrides[require_principal] = lambda: Principal("anonymous")
    try:
        client = TestClient(app)
        response = client.get("/api/v1/stocks/2330.TW/fundamentals?as_of=2026-11-28")
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"symbol", "as_of", "valuation", "revenue", "earnings", "notes"}
        assert set(body["valuation"]) == {
            "date",
            "pe_ratio",
            "pb_ratio",
            "dividend_yield_pct",
            "pe_percentile_250d",
            "pb_percentile_250d",
            "yield_percentile_250d",
            "history",
        }
        assert set(body["valuation"]["history"][0]) == {
            "date",
            "pe_ratio",
            "pb_ratio",
            "dividend_yield_pct",
        }
        assert set(body["revenue"]) == {
            "months",
            "yoy_3m_pct",
            "yoy_12m_pct",
            "is_12m_high",
            "growth_streak_months",
        }
        assert set(body["revenue"]["months"][0]) == {"period", "revenue", "yoy_pct", "mom_pct"}
        assert set(body["earnings"]) == {"quarters", "eps_ttm", "eps_yoy_pct", "roe_ttm_pct"}
        assert set(body["earnings"]["quarters"][0]) == {
            "period",
            "revenue",
            "gross_margin_pct",
            "operating_margin_pct",
            "net_margin_pct",
            "eps",
        }
        assert body["earnings"]["quarters"][-1]["period"] == "2026Q2"
        assert client.get("/api/v1/stocks/9998/fundamentals").status_code == 404
        past = client.get("/api/v1/stocks/2330.TW/fundamentals?as_of=2020-01-01").json()
        assert past["valuation"] is None and past["revenue"] is None and past["earnings"] is None
    finally:
        app.dependency_overrides.clear()


# --- migration and Postgres statements -----------------------------------------------------

MIGRATION = Path(__file__).parents[1] / "migrations" / "versions" / "0019_valuation_fundamentals.py"
TABLES = ("valuation_daily", "quarterly_financials")


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
    spec = importlib.util.spec_from_file_location("migration_0019", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    recorder = _Recorder()
    module.op = recorder
    assert (
        module.revision == "0019_valuation_fundamentals"
        and module.down_revision == "0017_holders_data"
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

    def mappings(self):
        return iter(())

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
    await repo.latest_valuation_metrics(cutoff)
    await repo.latest_valuation_metrics(cutoff, ["2330.TW"])
    await repo.valuation_history("2330.TW", cutoff)
    await repo.revenue_trends(cutoff)
    await repo.earnings_summaries(cutoff)
    await repo.quarterly_financials("2330.TW", cutoff)
    reads = list(session.sql)
    assert len(reads) == 6 and all("available_at <=" in sql for sql in reads)
    assert "FILTER (WHERE" in reads[0] and "row_number()" in reads[0]
    await repo.upsert_valuation_daily([day_row("2330.TW", date(2026, 11, 27), 20.0)])
    await repo.insert_missing_monthly_revenue([revenue_row("2026-10", 1.0)])
    await repo.upsert_quarterly_financials(STATEMENTS[:1])
    await repo.insert_missing_quarterly_financials(STATEMENTS[:1])
    assert any("ON CONFLICT DO NOTHING" in sql for sql in session.sql[6:])


# --- real Postgres (skipped without QUANT_TEST_DATABASE_URL) -------------------------------

DATABASE_URL = os.environ.get("QUANT_TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="QUANT_TEST_DATABASE_URL not set")
async def test_postgres_reads_match_the_memory_twin() -> None:
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from quant_signal.infrastructure.db import PostgresQuantRepository

    engine = create_async_engine(DATABASE_URL)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False)
    days = weekdays(300)
    symbols = ["ZZV1.TW", "ZZV2.TW"]

    def rows_for(symbol: str) -> list[ValuationDay]:
        return [
            day_row(
                symbol,
                d,
                None if i % 7 == 0 else float((i * 37) % 101),
                pb=1 + (i % 13) / 10,
                y=(i % 9) / 2,
            )
            for i, d in enumerate(days)
        ]

    revenue = [
        revenue_row(p, 100.0 + (i * 7) % 23).model_copy(update={"symbol": "ZZV1.TW"})
        for i, p in enumerate(month_periods("2023-09", 40))
    ]
    statements = [row.model_copy(update={"symbol": "ZZV1.TW"}) for row in STATEMENTS]
    memory = MemoryQuantRepository()
    try:
        async with factory() as session:
            repo = PostgresQuantRepository(session)
            for repository in (repo, memory):
                for symbol in symbols:
                    await repository.upsert_valuation_daily(rows_for(symbol))
                await repository.upsert_monthly_revenue(revenue)
                await repository.upsert_quarterly_financials(statements)
            assert await repo.upsert_valuation_daily(rows_for("ZZV1.TW")) == 0  # idempotent
            assert await repo.insert_missing_monthly_revenue(revenue) == 0
            assert await repo.insert_missing_quarterly_financials(statements) == 0
            restated = [statements[-1].model_copy(update={"eps": 9.9, "available_at": FAR})]
            assert await repo.upsert_quarterly_financials(restated) == 1
            assert await repo.upsert_quarterly_financials(restated) == 0
            cutoff = datetime(2026, 11, 28, tzinfo=TAIPEI)
            mine = {
                k: v
                for k, v in (await repo.latest_valuation_metrics(cutoff)).items()
                if k.startswith("ZZV")
            }
            theirs = {
                k: v
                for k, v in (await memory.latest_valuation_metrics(cutoff)).items()
                if k.startswith("ZZV")
            }
            assert mine == theirs and set(mine) == set(symbols)
            assert (
                mine["ZZV1.TW"].pe_observations > 60
                and mine["ZZV1.TW"].pe_percentile_250d is not None
            )
            assert (await repo.valuation_history("ZZV1.TW", cutoff, 50)) == (
                await memory.valuation_history("ZZV1.TW", cutoff, 50)
            )
            assert (await repo.revenue_trends(cutoff, ["ZZV1.TW"], include_months=True)) == (
                await memory.revenue_trends(cutoff, ["ZZV1.TW"], include_months=True)
            )
            assert (await repo.earnings_summaries(cutoff, ["ZZV1.TW"])) == (
                await memory.earnings_summaries(cutoff, ["ZZV1.TW"])
            )
            assert (await repo.earnings_summaries(FAR, ["ZZV1.TW"]))[
                "ZZV1.TW"
            ].eps == pytest.approx(9.9 - 1.2)
            assert [r.revision for r in await repo.quarterly_financials("ZZV1.TW", FAR)][-1] == 2
    finally:
        async with factory() as session:
            for table in ("valuation_daily", "monthly_revenue", "quarterly_financials"):
                await session.execute(sql_text(f"DELETE FROM {table} WHERE symbol LIKE 'ZZV%'"))
            await session.commit()
        await engine.dispose()
