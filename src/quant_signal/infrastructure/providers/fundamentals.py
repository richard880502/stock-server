"""Monthly-revenue history and quarterly statements (TWSE/TPEx OpenAPI and MOPS).

Verified live on 2026-11-28.

Forward (latest period only, JSON, ``Accept: application/json``):

- TWSE ``openapi.twse.com.tw/v1/opendata/t187ap06_L_<kind>`` (綜合損益表) and
  ``t187ap07_L_<kind>`` (資產負債表); TPEx ``www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_
  <kind>`` / ``mopsfin_t187ap07_O_<kind>``. ``kind``: ``ci`` 一般業 (full statement),
  ``basi`` 金融業, ``fh`` 金控業, ``ins`` 保險業, ``bd`` 證券期貨業, ``mim`` 異業. Every
  row carries ``年度`` (ROC year) / ``季別`` (1-4; TPEx ``Year``/``Season``) and
  ``公司代號`` (TPEx ``SecuritiesCompanyCode``). Amounts are NT$ thousands, *year-to-date
  cumulative* (115Q3 = Jan-Sep), EPS in NT$.

History (HTML, big5 for revenue / utf-8 for statements; whole market in one response):

- Monthly revenue ``https://mopsov.twse.com.tw/nas/t21/{sii|otc}/
  t21sc03_<ROC year>_<month>_0.html``:
  one table per industry, per company 公司代號, 公司名稱, 當月營收, 上月營收, 去年當月營收,
  上月比較增減(%), 去年同月增減(%), 當月累計營收, 去年累計營收, 前期比較增減(%) (NT$ thousands).
  A month not yet published returns a short page without tables.
- Quarterly statements POST ``https://mopsov.twse.com.tw/mops/web/ajax_t163sb04`` (income) and
  ``ajax_t163sb05`` (balance sheet) with ``TYPEK=sii|otc&year=<ROC>&season=<01-04>``: seven
  tables (banks, brokers, 一般業, holding companies, insurers, 異業...) whose column
  headers match the OpenAPI field names. ``查詢無資料`` when the quarter does not exist yet.

Backfilled rows are stamped with the *statutory publication deadline* (monthly revenue:
the 10th of the following month; general industries Q1 5/15, Q2 8/14, Q3 11/14, annual 3/31
-- end of day, Taipei; financial-sector companies file later, see ``financials_deadline``).
That is an approximation: most companies file earlier, and MOPS shows the
latest (possibly restated) figures, so history can differ slightly from what was known
on the day. Point-in-time readers should treat backfilled availability as conservative.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from quant_signal.domain.fundamentals import QuarterlyFinancial
from quant_signal.domain.models import MonthlyRevenue
from quant_signal.infrastructure.providers.official_events import parse_number

TAIPEI = ZoneInfo("Asia/Taipei")

MOPS_REVENUE_URL = "https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{year}_{month}_0.html"
MOPS_INCOME_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t163sb04"
MOPS_BALANCE_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t163sb05"
TWSE_INCOME_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_{kind}"
TWSE_BALANCE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_{kind}"
TPEX_INCOME_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_{kind}"
TPEX_BALANCE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap07_O_{kind}"
STATEMENT_KINDS = ("ci", "basi", "fh", "ins", "bd", "mim")

MOPS_REVENUE_SOURCE = "mops_revenue_history"
MOPS_FINANCIALS_SOURCE = "mops_financials"
TWSE_FINANCIALS_SOURCE = "twse_financials"
TPEX_FINANCIALS_SOURCE = "tpex_financials"

_COMMON_STOCK = re.compile(r"^[1-9]\d{3}$")
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TABLE = re.compile(r"<table[^>]*>(.*?)</table>", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")


# --- publication deadlines --------------------------------------------------------------


def revenue_deadline(period: str) -> datetime:
    """Statutory deadline for the month's revenue: the 10th of the next month, 23:59 Taipei."""
    year, month = (int(part) for part in period.split("-"))
    year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return datetime.combine(date(year, month, 10), time(23, 59), TAIPEI)


def financials_deadline(fiscal_year: int, fiscal_quarter: int, kind: str = "general") -> datetime:
    """Statutory filing deadline of a fiscal quarter (end of day, Taipei).

    General industries: Q1 5/15, Q2 8/14, Q3 11/14 of the same year, annual (Q4) 3/31 of
    the next year. Financial-sector companies (banks, insurers, financial holdings, bills
    finance, ...: ``kind == "financial"``) file later -- financial holdings are due about 60
    days after Q1/Q3 (5/30 and 11/29, moved to the next business day when that is a
    holiday) and two months after the half year (8/31). Their rule set is not one date, so
    the common later bound 5/31, 8/31, 11/30 and 3/31 is used, rolled to Monday when it
    falls on a weekend: a backfilled quarter never becomes visible before it existed.
    """
    if kind == "financial":
        due = {
            1: date(fiscal_year, 5, 31),
            2: date(fiscal_year, 8, 31),
            3: date(fiscal_year, 11, 30),
            4: date(fiscal_year + 1, 3, 31),
        }[fiscal_quarter]
        while due.weekday() >= 5:
            due += timedelta(days=1)
    else:
        due = {
            1: date(fiscal_year, 5, 15),
            2: date(fiscal_year, 8, 14),
            3: date(fiscal_year, 11, 14),
            4: date(fiscal_year + 1, 3, 31),
        }[fiscal_quarter]
    return datetime.combine(due, time(23, 59), TAIPEI)


# --- monthly revenue (MOPS history) -----------------------------------------------------


def mops_revenue_url(market: str, period: str) -> str:
    """``market`` is ``sii`` (listed) or ``otc``; ``period`` is ``YYYY-MM``."""
    year, month = (int(part) for part in period.split("-"))
    return MOPS_REVENUE_URL.format(market=market, year=year - 1911, month=month)


def _cell_text(cell: str) -> str:
    return unicodedata.normalize("NFKC", _TAGS.sub("", cell)).replace("\xa0", " ").strip()


def decode_mops_revenue(content: bytes) -> str:
    """The monthly pages are big5 (HKSCS superset) even though they say nothing in the header."""
    return content.decode("big5hkscs", errors="replace")


def parse_mops_revenue_html(
    text: str, *, period: str, suffix: str, available_at: datetime
) -> list[MonthlyRevenue]:
    """One market-month page -> rows; subtotal rows (``合計``) and non-stock codes are dropped.

    ``revenue`` is NT$ thousands. A row without a current-month figure is skipped (a
    company that has not filed shows ``-``).
    """
    rows: dict[str, MonthlyRevenue] = {}
    for line in _ROW.findall(text):
        cells = [_cell_text(cell) for cell in _CELL.findall(line)]
        if len(cells) < 10 or not _COMMON_STOCK.match(cells[0]):
            continue
        revenue = parse_number(cells[2])
        if revenue is None:
            continue
        rows[cells[0] + suffix] = MonthlyRevenue(
            symbol=cells[0] + suffix,
            period=period,
            revenue=revenue,
            mom_pct=parse_number(cells[5]),
            yoy_pct=parse_number(cells[6]),
            cumulative_revenue=parse_number(cells[7]),
            cumulative_yoy_pct=parse_number(cells[9]),
            source=MOPS_REVENUE_SOURCE,
            available_at=available_at,
        )
    return list(rows.values())


# --- quarterly statements ---------------------------------------------------------------


def _norm(key: Any) -> str:
    return unicodedata.normalize("NFKC", str(key)).replace(" ", "").strip()


def _find(
    row: dict[str, str],
    *,
    equals: tuple[str, ...] = (),
    prefix: tuple[str, ...] = (),
    contains: tuple[str, ...] = (),
) -> float | None:
    """First column whose normalised header is in ``equals`` or starts with a ``prefix``
    (and, when given, contains every ``contains`` word); its value parsed (blank -> None)."""
    for key, value in row.items():
        name = _norm(key)
        by_shape = bool(prefix or contains)
        if by_shape and prefix and not any(name.startswith(item) for item in prefix):
            by_shape = False
        if by_shape and not all(word in name for word in contains):
            by_shape = False
        if name in equals or by_shape:
            return parse_number(value)
    return None


def _has(row: dict[str, str], text: str) -> bool:
    return any(text in _norm(key) for key in row)


def statement_kind(income_row: dict[str, str]) -> str:
    """``general`` only for the full 一般業 statement (has 營業毛利); the rest is financial."""
    return (
        "general" if _has(income_row, "營業毛利") and _has(income_row, "營業收入") else "financial"
    )


def _income_figures(row: dict[str, str], kind: str) -> dict[str, float | None]:
    net_income = _find(row, prefix=("本期淨利", "本期稅後淨利"))
    parent = _find(row, prefix=("淨利",), contains=("歸屬於母公司業主",))
    minority = _find(row, prefix=("淨利",), contains=("歸屬於非控制權益",))
    if parent is None and not minority and net_income is not None:
        parent = net_income  # no non-controlling interest: owners get the whole profit
    figures: dict[str, float | None] = {
        "net_income_parent": parent,
        "eps": _find(row, contains=("基本每股盈餘",)),
    }
    if kind == "general":
        gross = _find(row, prefix=("營業毛利(毛損)淨額",))
        if gross is None:
            gross = _find(row, prefix=("營業毛利(毛損)",))
        figures.update(
            revenue=_find(row, equals=("營業收入",)),
            gross_profit=gross,
            operating_income=_find(row, prefix=("營業利益",)),
        )
    return figures


def _balance_figures(row: dict[str, str]) -> dict[str, float | None]:
    equity = _find(row, prefix=("歸屬於母公司業主",))
    minority = _find(row, equals=("非控制權益",))
    if equity is None and not minority:
        equity = _find(row, prefix=("權益總",))  # no non-controlling interest
    return {"equity_parent": equity, "total_assets": _find(row, prefix=("資產總",))}


def _code(row: dict[str, str]) -> str:
    for key in ("公司代號", "SecuritiesCompanyCode", "證券代號"):
        if key in row:
            return str(row[key]).strip()
    return ""


def _fiscal(row: dict[str, str]) -> tuple[int, int] | None:
    year = next((str(row[k]).strip() for k in ("年度", "Year") if k in row), "")
    season = next((str(row[k]).strip() for k in ("季別", "Season") if k in row), "")
    if not year.isdigit() or not season.isdigit() or not 1 <= int(season) <= 4:
        return None
    number = int(year)
    return (number + 1911 if number < 1911 else number), int(season)


def build_quarterly_financials(
    income: list[dict[str, str]],
    balance: list[dict[str, str]],
    *,
    suffix: str,
    source: str,
    available_at: datetime | None = None,
    fiscal: tuple[int, int] | None = None,
) -> list[QuarterlyFinancial]:
    """Join one market's income and balance-sheet rows into one row per company-quarter.

    ``fiscal`` (year, quarter) overrides the per-row 年度/季別 (the MOPS tables have no such
    columns); without it rows lacking a year/quarter are skipped. ``available_at`` None
    stamps each row with its statutory deadline (history); pass the fetch time for live
    rows. Companies with an income row but no balance row keep equity/assets as None.
    """
    balances: dict[tuple[str, int, int], dict[str, float | None]] = {}
    for row in balance:
        symbol, period = _code(row), fiscal or _fiscal(row)
        if _COMMON_STOCK.match(symbol) and period:
            balances[(symbol, *period)] = _balance_figures(row)
    result: dict[tuple[str, int, int], QuarterlyFinancial] = {}
    for row in income:
        symbol, period = _code(row), fiscal or _fiscal(row)
        if not _COMMON_STOCK.match(symbol) or not period:
            continue
        kind = statement_kind(row)
        figures = _income_figures(row, kind)
        if figures["eps"] is None and figures["net_income_parent"] is None:
            continue  # nothing usable (e.g. an empty placeholder row)
        figures.update(balances.get((symbol, *period), {}))
        result[(symbol, *period)] = QuarterlyFinancial(
            symbol=symbol + suffix,
            fiscal_year=period[0],
            fiscal_quarter=period[1],
            statement_kind=kind,  # type: ignore[arg-type]
            source=source,
            available_at=available_at or financials_deadline(*period, kind),
            **figures,
        )
    return list(result.values())


def parse_mops_statement_html(text: str) -> list[dict[str, str]]:
    """Every company row of every table in an ``ajax_t163sb04/05`` response, keyed by header.

    Raises ``ValueError`` unless the page is a recognisable result or an explicit "no
    data" page (``查詢無資料``), so a blocked/changed page is never read as "empty".
    """
    rows: list[dict[str, str]] = []
    found_table = False
    for table in _TABLE.findall(text):
        lines = _ROW.findall(table)
        if not lines:
            continue
        headers = [
            _cell_text(re.sub(r"<br\s*/?>", "", cell, flags=re.I))
            for cell in _CELL.findall(lines[0])
        ]
        if not headers or "公司代號" not in headers[0]:
            continue
        found_table = True
        headers[0] = "公司代號"
        for line in lines[1:]:
            cells = [_cell_text(cell) for cell in _CELL.findall(line)]
            if len(cells) == len(headers) and _COMMON_STOCK.match(cells[0]):
                rows.append(dict(zip(headers, cells, strict=True)))
    if not found_table and "查詢無資料" not in text:
        raise ValueError("unrecognised MOPS statement page")
    return rows


def mops_statement_form(market: str, fiscal_year: int, fiscal_quarter: int) -> dict[str, str]:
    return {
        "encodeURIComponent": "1",
        "step": "1",
        "firstin": "1",
        "off": "1",
        "isQuery": "Y",
        "TYPEK": market,
        "year": str(fiscal_year - 1911),
        "season": f"{fiscal_quarter:02d}",
    }
