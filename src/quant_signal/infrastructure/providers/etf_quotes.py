"""ETF rows of the daily whole-market reports the providers already download.

TWSE ``MI_INDEX`` (type ALLBUT0999) and TPEx ``dailyQuotes`` list every ETF next to the
ordinary stocks, so these parsers read the same payload the stock parsers do; no request
of their own. See ``domain/etf.py`` for the inclusion rule. A row without a trade
(``--`` / ``---`` prices on a day nothing traded) is skipped, never filled in: the previous
stored close stays the reference.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError

from quant_signal.domain.etf import EtfDay, is_etf_code
from quant_signal.infrastructure.providers.market_snapshot import to_int


def _price(value: Any) -> float:
    text = str(value or "").strip().replace(",", "")
    if text in {"", "--", "---", "除權", "除息"}:
        raise ValueError(f"no trade: {value!r}")
    return float(text)


def _payload_date(payload: dict[str, Any]) -> date | None:
    requested = str(payload.get("date") or "")
    if len(requested) != 8 or not requested.isdigit():
        return None
    return date.fromisoformat(f"{requested[:4]}-{requested[4:6]}-{requested[6:]}")


def parse_twse_etfs(
    payload: dict[str, Any], *, available_at: datetime, source: str
) -> list[EtfDay]:
    """ETF rows of a TWSE MI_INDEX payload (``[]`` when the day has no report)."""
    trading_date = _payload_date(payload)
    if trading_date is None or payload.get("stat") != "OK":
        return []
    rows: list[list[Any]] = []
    for table in payload.get("tables") or []:
        fields = table.get("fields") or []
        if fields[:3] == ["證券代號", "證券名稱", "成交股數"]:
            rows = table.get("data") or []
    result: list[EtfDay] = []
    for row in rows:
        try:
            code = str(row[0]).strip()
            if not is_etf_code(code):
                continue
            sign = re.sub(r"<[^>]+>", "", str(row[9])).strip()
            direction = 1 if "+" in sign else -1 if "-" in sign else 0
            result.append(
                EtfDay(
                    symbol=f"{code}.TW",
                    name=str(row[1]).strip(),
                    market="TW",
                    trading_date=trading_date,
                    open=_price(row[5]),
                    high=_price(row[6]),
                    low=_price(row[7]),
                    close=_price(row[8]),
                    # ``X`` (ex-rights/dividend) rows print 0.00: the reference was adjusted
                    # and the report does not give it, so the change stays unknown (0).
                    change=direction * abs(_price(row[10])) if direction else 0.0,
                    volume=to_int(row[2]),
                    turnover_value=_price(row[4]),
                    trades=to_int(row[3]),
                    available_at=available_at,
                    source=source,
                )
            )
        except (IndexError, TypeError, ValueError, ValidationError):
            continue
    return result


def parse_tpex_etfs(
    payload: dict[str, Any], *, available_at: datetime, source: str
) -> list[EtfDay]:
    """ETF rows of a TPEx dailyQuotes payload (``[]`` when the day has no report)."""
    trading_date = _payload_date(payload)
    tables = payload.get("tables") or []
    if trading_date is None or not tables:
        return []
    result: list[EtfDay] = []
    for row in tables[0].get("data") or []:
        try:
            code = str(row[0]).strip()
            if not is_etf_code(code):
                continue
            result.append(
                EtfDay(
                    symbol=f"{code}.TWO",
                    name=str(row[1]).strip(),
                    market="TWO",
                    trading_date=trading_date,
                    open=_price(row[4]),
                    high=_price(row[5]),
                    low=_price(row[6]),
                    close=_price(row[2]),
                    change=float(str(row[3]).strip().replace(",", "").replace("+", "") or 0),
                    volume=to_int(row[8]),
                    turnover_value=_price(row[9]),
                    trades=to_int(row[10]),
                    available_at=available_at,
                    source=source,
                )
            )
        except (IndexError, TypeError, ValueError, ValidationError):
            continue
    return result
