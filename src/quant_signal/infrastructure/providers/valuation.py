"""Daily P/E, P/B and dividend yield for the whole market (TWSE and TPEx, no key).

Endpoints (verified 2026-11-28; any past trading day works, which is what the backfill
uses; a holiday / future date answers "no data"):

- TWSE ``rwd/zh/afterTrading/BWIBBU_d?date=YYYYMMDD&selectType=ALL&response=json``:
  ``{"stat": "OK", "date": "20261127", "fields": [證券代號, 證券名稱, 收盤價, 殖利率(%),
  股利年度, 本益比, 股價淨值比, 財報年/季], "data": [[...]]}``. Loss-making companies carry
  ``"-"`` as P/E; 股利年度 is an ROC year (114); 財報年/季 looks like ``"115/3"``. A holiday
  answers ``{"stat": "很抱歉，沒有符合條件的資料!", "total": 0}``.
- TPEx ``web/stock/aftertrading/peratio_analysis/pera_result.php?l=zh-tw&d=115/11/27&o=json``:
  ``{"tables": [{"date": "115/11/27", "totalCount": 895, "fields": [股票代號, 公司名稱,
  本益比, 每股股利, 股利年度, 殖利率(%), 股價淨值比, 財報年/季], "data": [[...]]}]}``;
  P/E ``"N/A"`` for losses, names padded with spaces, 財報年/季 ``"115Q3"``; a holiday has
  ``totalCount`` 0 and no rows.

Both publish P/E from the trailing four quarters of after-tax profit; the yield is cash
dividend of the 股利年度 over the close. A yield of 0.00 means no dividend (a real zero),
while a blank/``-`` is None.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from quant_signal.domain.fundamentals import ValuationDay
from quant_signal.infrastructure.providers.official_events import parse_number, parse_roc_date

TWSE_VALUATION_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d"
TPEX_VALUATION_URL = (
    "https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php"
)
TWSE_VALUATION_SOURCE = "twse_bwibbu"
TPEX_VALUATION_SOURCE = "tpex_pera"

_COMMON_STOCK = re.compile(r"^[1-9]\d{3}$")
_PERIOD = re.compile(r"^(\d{2,4})\s*[/Qq\-]\s*(\d)$")


def twse_valuation_params(day: date) -> dict[str, str]:
    return {"date": day.strftime("%Y%m%d"), "selectType": "ALL", "response": "json"}


def tpex_valuation_params(day: date) -> dict[str, str]:
    return {"l": "zh-tw", "d": f"{day.year - 1911}/{day.month:02d}/{day.day:02d}", "o": "json"}


def parse_fiscal_period(value: Any) -> str | None:
    """``"115/3"`` (TWSE) and ``"115Q3"`` (TPEx) -> ``"2026Q3"``; anything else None."""
    match = _PERIOD.match(str(value or "").strip())
    if not match:
        return None
    year, quarter = int(match.group(1)), int(match.group(2))
    if year < 1911:
        year += 1911
    return f"{year}Q{quarter}" if 1 <= quarter <= 4 else None


def _dividend_year(value: Any) -> int | None:
    number = parse_number(value)
    if number is None or number <= 0:
        return None
    year = int(number)
    return year + 1911 if year < 1911 else year


def _index(fields: list[Any], *names: str) -> int:
    for name in names:
        if name in fields:
            return fields.index(name)
    raise ValueError(f"unexpected valuation columns: missing {names[0]}")


def _rows(
    fields: list[Any],
    data: list[Any],
    day: date,
    *,
    code: str,
    suffix: str,
    source: str,
    available_at: datetime,
) -> list[ValuationDay]:
    code_i = _index(fields, code)
    pe_i = _index(fields, "本益比")
    pb_i = _index(fields, "股價淨值比")
    yield_i = _index(fields, "殖利率(%)")
    year_i = _index(fields, "股利年度")
    period_i = _index(fields, "財報年/季")
    width = max(code_i, pe_i, pb_i, yield_i, year_i, period_i) + 1
    rows: dict[str, ValuationDay] = {}
    for item in data:
        if not isinstance(item, list) or len(item) < width:
            continue
        stock = str(item[code_i]).strip()
        if not _COMMON_STOCK.match(stock):
            continue
        pe = parse_number(item[pe_i])
        pb = parse_number(item[pb_i])
        dividend_yield = parse_number(item[yield_i])
        rows[stock + suffix] = ValuationDay(
            symbol=stock + suffix,
            trading_date=day,
            source=source,
            # P/E is undefined for a zero/negative EPS; the exchanges print "-"/"N/A".
            pe_ratio=pe if pe is not None and pe > 0 else None,
            pb_ratio=pb if pb is not None and pb > 0 else None,
            dividend_yield_pct=dividend_yield if dividend_yield is not None else None,
            dividend_year=_dividend_year(item[year_i]),
            fiscal_period=parse_fiscal_period(item[period_i]),
            available_at=available_at,
        )
    return list(rows.values())


def parse_twse_valuation(payload: Any, *, available_at: datetime) -> list[ValuationDay]:
    """TWSE BWIBBU_d; any ``stat`` other than OK (holiday, future date) yields no rows."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    day = parse_roc_date(payload.get("date"))
    if day is None:
        raise ValueError("BWIBBU_d payload has no date")
    return _rows(
        payload.get("fields") or [],
        payload.get("data") or [],
        day,
        code="證券代號",
        suffix=".TW",
        source=TWSE_VALUATION_SOURCE,
        available_at=available_at,
    )


def parse_tpex_valuation(payload: Any, *, available_at: datetime) -> list[ValuationDay]:
    """TPEx 個股本益比、殖利率、股價淨值比 (one table; empty ``data`` on a holiday)."""
    if not isinstance(payload, dict) or str(payload.get("stat", "ok")).lower() != "ok":
        return []
    rows: dict[str, ValuationDay] = {}
    for table in payload.get("tables") or []:
        if not table.get("data"):
            continue
        day = parse_roc_date(table.get("date"))
        if day is None:
            raise ValueError("TPEx valuation table has no date")
        for row in _rows(
            table.get("fields") or [],
            table["data"],
            day,
            code="股票代號",
            suffix=".TWO",
            source=TPEX_VALUATION_SOURCE,
            available_at=available_at,
        ):
            rows[row.symbol] = row
    return list(rows.values())
