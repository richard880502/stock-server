"""Official short-selling / day-trading / insider-pledge / dividend-forecast sources.

All endpoints verified live on 2026-11-28 (public, no key). Units are shares unless noted.

- TWSE ``rwd/zh/marginTrading/TWT93U?date=YYYYMMDD`` 信用額度總量管制餘額表 (header
  ``單位：股``): 15 positional columns, ``代號, 名稱`` then 融券 ``前日餘額, 賣出, 買進, 現券,
  今日餘額, 次一營業日限額`` then 借券賣出 ``前日餘額, 當日賣出, 當日還券, 當日調整, 當日餘額,
  次一營業日可限額``, ``備註``. History is available for past dates; a non-trading day answers
  ``stat OK`` with an empty ``data``; a future date answers a non-OK ``stat``. Refreshed
  around 20:30 and 22:30 Taipei. Margin short (融券) is NOT stored: ``market_stock_daily``
  already has it (in lots).
- TPEx ``web/stock/margin_trading/margin_sbl/margin_sbl_result.php?l=zh-tw&d=ROC&o=json``:
  the same table for OTC stocks (``tables[0]``, 15 columns in the same order, ``date`` as
  ``YYYYMMDD``, ``totalCount`` 0 on non-trading days, history available).
- TWSE ``rwd/zh/dayTrading/TWTB4U?date=YYYYMMDD`` 當日沖銷交易標的及成交量值: ``tables``
  holds a market summary and the per-stock table whose fields include 當日沖銷交易成交股數 /
  買進成交金額 / 賣出成交金額 (shares, NT$). On non-trading days or not-yet-published days
  the per-stock table only lists *targets* (no volume field) and a future date is answered
  with the NEXT trading day's target list, so rows are accepted only when the volume
  field exists and the payload date matches. TWSE re-publishes T on T+1 and T+2 (final).
  TPEx publishes only the target list and market totals, no per-stock volume: skipped.
- Insider holdings and pledges: TWSE OpenAPI ``opendata/t187ap11_L`` and TPEx OpenAPI
  ``mopsfin_t187ap11_O`` (董監事、經理人及大股東持股餘額明細), one row per insider, monthly
  (``資料年月`` ROC ``11510`` = 2026-10). One person can appear under several titles, so rows
  are de-duplicated per (company, name) before summing.
- Dividend forecast: TWSE ``rwd/zh/exRight/TWT48U`` 除權除息預告表 (upcoming only) and TPEx
  OpenAPI ``tpex_exright_prepost``. 無償配股率 / StockDividendRatio is new shares per 1 share
  held (0.0241 = 24 shares per 1,000), 現金股利 is NT$ per share.

Parsers are pure (payload in, typed rows out). Only four-digit non-zero-prefixed common
stock codes are kept, matching ``market_stock_daily`` symbols.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from quant_signal.domain.etf import is_etf_code
from quant_signal.domain.short_data import (
    DayTradeDay,
    DividendEvent,
    DividendKind,
    InsiderHolding,
    ShortLendingDay,
)
from quant_signal.infrastructure.providers.official_events import parse_number, parse_roc_date

TWSE_SBL_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U"
TPEX_SBL_URL = "https://www.tpex.org.tw/web/stock/margin_trading/margin_sbl/margin_sbl_result.php"
TWSE_DAYTRADE_URL = "https://www.twse.com.tw/rwd/zh/dayTrading/TWTB4U"
TWSE_PLEDGE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap11_L"
TPEX_PLEDGE_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap11_O"
TWSE_DIVIDEND_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT48U"
TPEX_DIVIDEND_URL = "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"

TWSE_SBL_SOURCE = "twse_twt93u"
TPEX_SBL_SOURCE = "tpex_margin_sbl"
TWSE_DAYTRADE_SOURCE = "twse_twtb4u"
TWSE_PLEDGE_SOURCE = "twse_t187ap11"
TPEX_PLEDGE_SOURCE = "tpex_t187ap11"
TWSE_DIVIDEND_SOURCE = "twse_twt48u"
TPEX_DIVIDEND_SOURCE = "tpex_exright_prepost"

_COMMON_STOCK = re.compile(r"^[1-9]\d{3}$")
_SBL_COLUMNS = 15


def twse_params(day: date) -> dict[str, str]:
    return {"date": day.strftime("%Y%m%d"), "response": "json"}


def tpex_sbl_params(day: date) -> dict[str, str]:
    return {"l": "zh-tw", "d": f"{day.year - 1911}/{day.month:02d}/{day.day:02d}", "o": "json"}


def _int(value: Any) -> int | None:
    number = parse_number(value)
    return None if number is None else int(round(number))


def _gregorian(value: Any) -> date | None:
    return parse_roc_date(value)


# --- securities-lending short selling --------------------------------------------------


def _sbl_rows(
    data: Any, day: date, *, suffix: str, source: str, available_at: datetime
) -> list[ShortLendingDay]:
    rows: dict[str, ShortLendingDay] = {}
    for item in data or []:
        if not isinstance(item, list) or len(item) < _SBL_COLUMNS - 1:
            continue
        code = str(item[0]).strip()
        if not _COMMON_STOCK.match(code):
            continue
        row = ShortLendingDay(
            symbol=code + suffix,
            trading_date=day,
            source=source,
            sbl_prev_balance=_int(item[8]),
            sbl_sold=_int(item[9]),
            sbl_returned=_int(item[10]),
            sbl_adjustment=_int(item[11]),
            sbl_balance=_int(item[12]),
            sbl_limit_next_day=_int(item[13]),
            available_at=available_at,
        )
        if row.sbl_balance is None:
            continue  # nothing usable on the row
        rows[row.symbol] = row
    return list(rows.values())


def parse_twse_sbl(payload: Any, *, available_at: datetime) -> list[ShortLendingDay]:
    """TWSE TWT93U; a non-OK ``stat`` (future date) or an empty table yields no rows."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK" or not payload.get("data"):
        return []
    day = _gregorian(payload.get("date"))
    fields = payload.get("fields") or []
    if day is None or len(fields) != _SBL_COLUMNS or fields[0] != "代號":
        raise ValueError("unexpected TWT93U layout")
    return _sbl_rows(
        payload["data"], day, suffix=".TW", source=TWSE_SBL_SOURCE, available_at=available_at
    )


def parse_tpex_sbl(payload: Any, *, available_at: datetime) -> list[ShortLendingDay]:
    """TPEx margin_sbl_result (same 15 columns, ``tables[0]``)."""
    if not isinstance(payload, dict) or str(payload.get("stat", "ok")).lower() != "ok":
        return []
    rows: list[ShortLendingDay] = []
    for table in payload.get("tables") or []:
        data = table.get("data")
        if not data:
            continue
        day = _gregorian(table.get("date") or payload.get("date"))
        fields = table.get("fields") or []
        if day is None or len(fields) != _SBL_COLUMNS:
            raise ValueError("unexpected TPEx margin_sbl layout")
        rows += _sbl_rows(
            data, day, suffix=".TWO", source=TPEX_SBL_SOURCE, available_at=available_at
        )
    return rows


# --- day trading -----------------------------------------------------------------------


def parse_twse_daytrade(payload: Any, *, available_at: datetime) -> list[DayTradeDay]:
    """TWSE TWTB4U per-stock table (target-list-only tables carry no volume and are ignored)."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    day = _gregorian(payload.get("date"))
    rows: dict[str, DayTradeDay] = {}
    for table in payload.get("tables") or []:
        fields = [str(name).replace("\n", "") for name in table.get("fields") or []]
        if "當日沖銷交易成交股數" not in fields:
            continue
        if day is None:
            raise ValueError("TWTB4U payload has no date")
        try:
            code_i = fields.index("證券代號")
            vol_i = fields.index("當日沖銷交易成交股數")
            buy_i = fields.index("當日沖銷交易買進成交金額")
            sell_i = fields.index("當日沖銷交易賣出成交金額")
        except ValueError as exc:
            raise ValueError("unexpected TWTB4U columns") from exc
        for item in table.get("data") or []:
            if not isinstance(item, list) or len(item) <= max(code_i, vol_i, buy_i, sell_i):
                continue
            code = str(item[code_i]).strip()
            volume = _int(item[vol_i])
            if not _COMMON_STOCK.match(code) or volume is None:
                continue
            rows[code + ".TW"] = DayTradeDay(
                symbol=code + ".TW",
                trading_date=day,
                source=TWSE_DAYTRADE_SOURCE,
                day_trade_volume=volume,
                day_trade_buy_amount=_int(item[buy_i]),
                day_trade_sell_amount=_int(item[sell_i]),
                available_at=available_at,
            )
    return list(rows.values())


# --- insider holdings and pledges ------------------------------------------------------


def _field(item: dict[str, Any], key: str) -> Any:
    if key in item:
        return item[key]
    for name, value in item.items():
        if isinstance(name, str) and name.strip() == key:
            return value
    return None


def _period(value: Any) -> str | None:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{5}", text):
        return None
    year, month = int(text[:3]) + 1911, int(text[3:])
    return f"{year}-{month:02d}" if 1 <= month <= 12 else None


def _parse_pledges(
    payload: Any, *, suffix: str, source: str, available_at: datetime
) -> list[InsiderHolding]:
    if not isinstance(payload, list):
        raise ValueError("pledge payload must be a JSON array")
    people: dict[tuple[str, str, str], tuple[int, int]] = {}
    published: dict[tuple[str, str], date | None] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        code = str(_field(item, "公司代號") or "").strip()
        period = _period(_field(item, "資料年月"))
        name = str(_field(item, "姓名") or "").strip()
        held = _int(_field(item, "目前持股"))
        pledged = _int(_field(item, "設質股數"))
        if not _COMMON_STOCK.match(code) or period is None or not name or held is None:
            continue
        key = (code, period, name)
        old_held, old_pledged = people.get(key, (0, 0))
        # The same person under several titles repeats the same holding: keep the larger.
        people[key] = (max(old_held, held), max(old_pledged, pledged or 0))
        published[(code, period)] = _gregorian(_field(item, "出表日期"))
    totals: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    for (code, period, _), (held, pledged) in people.items():
        bucket = totals[(code, period)]
        bucket[0] += 1
        bucket[1] += held
        bucket[2] += pledged
    rows = []
    for (code, period), (count, held, pledged) in sorted(totals.items()):
        rows.append(
            InsiderHolding(
                symbol=code + suffix,
                period=period,
                source=source,
                insider_count=count,
                holding_shares=held,
                pledged_shares=pledged,
                pledge_ratio_pct=round(pledged / held * 100, 4) if held > 0 else None,
                published_on=published.get((code, period)),
                available_at=available_at,
            )
        )
    return rows


def parse_twse_pledges(payload: Any, *, available_at: datetime) -> list[InsiderHolding]:
    return _parse_pledges(
        payload, suffix=".TW", source=TWSE_PLEDGE_SOURCE, available_at=available_at
    )


def parse_tpex_pledges(payload: Any, *, available_at: datetime) -> list[InsiderHolding]:
    return _parse_pledges(
        payload, suffix=".TWO", source=TPEX_PLEDGE_SOURCE, available_at=available_at
    )


# --- dividend forecast -----------------------------------------------------------------

_KIND: dict[str, DividendKind] = {
    "息": "ex_dividend",
    "除息": "ex_dividend",
    "權": "ex_rights",
    "除權": "ex_rights",
    "權息": "ex_rights_dividend",
    "除權息": "ex_rights_dividend",
}


def _dividend_code(code: str) -> bool:
    """Ordinary stocks and ETFs (收益分配): ETFs pay cash distributions, often monthly or
    quarterly. ETNs (02xxxx) and 受益證券 (01xxxT), which the feeds list too, stay out."""
    return bool(_COMMON_STOCK.match(code)) or is_etf_code(code)


def _amount(value: Any) -> float | None:
    """A number, or None for blanks, ``待公告...`` HTML, ``尚未公告``, ``註4`` and the like."""
    text = str(value if value is not None else "")
    if "<" in text:
        return None
    return parse_number(text)


def parse_twse_dividends(payload: Any, *, available_at: datetime) -> list[DividendEvent]:
    """TWSE TWT48U upcoming 除權除息預告表."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    fields = [str(name).replace("<br>", "") for name in payload.get("fields") or []]
    try:
        idx = {
            "date": fields.index("除權除息日期"),
            "code": fields.index("股票代號"),
            "kind": fields.index("除權息"),
            "stock": fields.index("無償配股率"),
            "cap": fields.index("現金增資配股率"),
            "price": fields.index("現金增資認購價"),
            "cash": fields.index("現金股利"),
        }
    except ValueError as exc:
        raise ValueError("unexpected TWT48U columns") from exc
    events: dict[tuple[str, date, str], DividendEvent] = {}
    for item in payload.get("data") or []:
        if not isinstance(item, list) or len(item) <= max(idx.values()):
            continue
        code = str(item[idx["code"]]).strip()
        ex_date = parse_roc_date(item[idx["date"]])
        kind = _KIND.get(str(item[idx["kind"]]).strip())
        if not _dividend_code(code) or ex_date is None or kind is None:
            continue
        event = DividendEvent(
            symbol=code + ".TW",
            ex_date=ex_date,
            kind=kind,
            source=TWSE_DIVIDEND_SOURCE,
            cash_dividend_per_share=_amount(item[idx["cash"]]),
            stock_dividend_per_share=_amount(item[idx["stock"]]),
            cash_capital_increase_ratio=_amount(item[idx["cap"]]),
            subscription_price=_amount(item[idx["price"]]) or None,
            available_at=available_at,
        )
        events[(event.symbol, ex_date, kind)] = event
    return list(events.values())


def parse_tpex_dividends(payload: Any, *, available_at: datetime) -> list[DividendEvent]:
    """TPEx ``tpex_exright_prepost`` (OpenAPI, upcoming only)."""
    if not isinstance(payload, list):
        raise ValueError("TPEx exright payload must be a JSON array")
    events: dict[tuple[str, date, str], DividendEvent] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        code = str(item.get("SecuritiesCompanyCode") or "").strip()
        ex_date = parse_roc_date(item.get("ExRrightsExDividendDate"))
        kind = _KIND.get(str(item.get("ExRrightsExDividend") or "").strip())
        if not _dividend_code(code) or ex_date is None or kind is None:
            continue
        event = DividendEvent(
            symbol=code + ".TWO",
            ex_date=ex_date,
            kind=kind,
            source=TPEX_DIVIDEND_SOURCE,
            cash_dividend_per_share=_amount(item.get("CashDividend")),
            stock_dividend_per_share=_amount(item.get("StockDividendRatio")),
            cash_capital_increase_ratio=_amount(item.get("SubscriptionRatioToNewSharesIssued")),
            subscription_price=_amount(item.get("SubscriptionPricePerShare")) or None,
            available_at=available_at,
        )
        events[(event.symbol, ex_date, kind)] = event
    return list(events.values())
