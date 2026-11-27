"""Whole-market holder data: TDCC weekly shareholding distribution and TWSE/TPEx
foreign holding with shares outstanding.

Endpoints (all public, no key):

- TDCC 集保戶股權分散表 open data ``getOD.ashx?id=1-5``: one UTF-8 (BOM) CSV with the
  *latest week only* for every security, CRLF lines, columns ``資料日期`` (Gregorian
  ``YYYYMMDD``, the Friday), ``證券代號`` (space-padded to 6), ``持股分級`` 1-17, ``人數``,
  ``股數``, ``占集保庫存數比例%``. Levels (shares held): 1 = 1-999, 2 = 1,000-5,000,
  3 = 5,001-10,000, 4 = 10,001-15,000, 5 = 15,001-20,000, 6 = 20,001-30,000,
  7 = 30,001-40,000, 8 = 40,001-50,000, 9 = 50,001-100,000, 10 = 100,001-200,000,
  11 = 200,001-400,000, 12 = 400,001-600,000, 13 = 600,001-800,000,
  14 = 800,001-1,000,000, 15 = more than 1,000,000, 16 = 差異數調整 (reconciliation),
  17 = 合計 (total). History accumulates from the day we start fetching.
- TWSE ``rwd/zh/fund/MI_QFIIS?date=YYYYMMDD&selectType=ALLBUT0999``: JSON, one row per
  security with 發行股數, 外資及陸資尚可投資股數, 全體外資及陸資持有股數 and the holding
  ratio (numbers with thousands commas).
- TPEx ``web/stock/3insti/qfii/qfii_result.php?l=zh-tw&d=<ROC date>&o=json``: the same
  table for OTC stocks (ROC date ``115/11/26``; ``totalCount`` 0 on non-trading days).

Parsers are pure (payload in, typed rows out). Only symbols with a known exchange
(TDCC) or four-digit non-zero-prefixed common-stock codes (foreign holding) are kept,
matching ``market_stock_daily`` symbols such as ``2330.TW`` / ``6488.TWO``.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from quant_signal.domain.models import (
    ForeignHolding,
    ShareholdingDistribution,
    ShareholdingTier,
)
from quant_signal.infrastructure.providers.official_events import parse_number, parse_roc_date

TDCC_URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
TWSE_QFIIS_URL = "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS"
TPEX_QFII_URL = "https://www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php"

TDCC_SOURCE = "tdcc_opendata"
TWSE_QFIIS_SOURCE = "twse_qfiis"
TPEX_QFII_SOURCE = "tpex_qfii"

RETAIL_LEVELS = (1, 2, 3)  # 10,000 shares (10 lots) or fewer
GE400_LEVELS = (12, 13, 14, 15)  # more than 400,000 shares (400 lots)
GE1000_LEVELS = (15,)  # more than 1,000,000 shares (1,000 lots)
TOTAL_LEVEL = 17
ADJUSTMENT_LEVEL = 16

_COMMON_STOCK = re.compile(r"^[1-9]\d{3}$")


def twse_qfiis_params(day: date) -> dict[str, str]:
    return {"date": day.strftime("%Y%m%d"), "selectType": "ALLBUT0999", "response": "json"}


def tpex_qfii_params(day: date) -> dict[str, str]:
    return {"l": "zh-tw", "d": f"{day.year - 1911}/{day.month:02d}/{day.day:02d}", "o": "json"}


def _int(value: Any) -> int | None:
    number = parse_number(value)
    return None if number is None else int(number)


def _sum_levels(
    levels: dict[int, tuple[int, int, float]], chosen: tuple[int, ...]
) -> tuple[int, int]:
    return (
        sum(levels.get(lv, (0, 0, 0.0))[0] for lv in chosen),
        sum(levels.get(lv, (0, 0, 0.0))[1] for lv in chosen),
    )


def _pct(part: int, whole: int) -> float:
    return round(part / whole * 100, 4) if whole else 0.0


# --- TDCC -----------------------------------------------------------------------------


def parse_tdcc_distribution(
    text: str, *, markets: dict[str, str], available_at: datetime
) -> list[ShareholdingDistribution]:
    """Parse the TDCC CSV.

    ``markets`` maps a bare code (``"2330"``) to its symbol (``"2330.TW"``); securities
    not in it (ETFs, warrants, bonds...) are skipped. A security with a missing total row
    or an unparsable row is dropped whole rather than stored half-right.
    """
    reader = csv.reader(io.StringIO(text.lstrip("﻿"), newline=""))
    groups: dict[tuple[date, str], dict[int, tuple[int, int, float]]] = defaultdict(dict)
    broken: set[str] = set()
    for record in reader:
        if len(record) < 6:
            continue  # blank line / header fragment
        code = record[1].strip()
        symbol = markets.get(code)
        if symbol is None:
            continue
        data_date = parse_roc_date(record[0])
        level, holders, shares = _int(record[2]), _int(record[3]), _int(record[4])
        percent = parse_number(record[5])
        if (
            data_date is None
            or level is None
            or holders is None
            or shares is None
            or percent is None
            or not 1 <= level <= TOTAL_LEVEL
        ):
            broken.add(symbol)
            continue
        groups[(data_date, symbol)][level] = (holders, shares, percent)

    rows: list[ShareholdingDistribution] = []
    for (data_date, symbol), levels in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if symbol in broken or TOTAL_LEVEL not in levels:
            continue
        total_holders, total_shares, _ = levels[TOTAL_LEVEL]
        if total_shares <= 0:
            continue

        ge400_holders, ge400_shares = _sum_levels(levels, GE400_LEVELS)
        ge1000_holders, ge1000_shares = _sum_levels(levels, GE1000_LEVELS)
        retail_holders, retail_shares = _sum_levels(levels, RETAIL_LEVELS)
        tiers = [
            ShareholdingTier(level=lv, holders=h, shares=s, pct=p)
            for lv, (h, s, p) in sorted(levels.items())
            if lv <= ADJUSTMENT_LEVEL
        ]
        rows.append(
            ShareholdingDistribution(
                symbol=symbol,
                data_date=data_date,
                total_holders=total_holders,
                total_shares=total_shares,
                holders_ge400=ge400_holders,
                pct_ge400=_pct(ge400_shares, total_shares),
                holders_ge1000=ge1000_holders,
                pct_ge1000=_pct(ge1000_shares, total_shares),
                retail_holders=retail_holders,
                retail_pct=_pct(retail_shares, total_shares),
                tiers=tiers,
                source=TDCC_SOURCE,
                available_at=available_at,
            )
        )
    return rows


# --- foreign holding ------------------------------------------------------------------


def _foreign_row(
    code: str,
    day: date,
    outstanding: Any,
    held: Any,
    pct: Any,
    *,
    suffix: str,
    source: str,
    available_at: datetime,
) -> ForeignHolding | None:
    code = code.strip()
    shares_out, foreign = _int(outstanding), _int(held)
    if not _COMMON_STOCK.match(code) or not shares_out or shares_out <= 0 or foreign is None:
        return None
    percent = parse_number(pct)
    return ForeignHolding(
        symbol=code + suffix,
        trading_date=day,
        shares_outstanding=shares_out,
        foreign_shares=foreign,
        foreign_holding_pct=percent if percent is not None else _pct(foreign, shares_out),
        source=source,
        available_at=available_at,
    )


def parse_twse_qfiis(payload: Any, *, available_at: datetime) -> list[ForeignHolding]:
    """TWSE MI_QFIIS; ``stat`` other than OK (holiday, future date) yields no rows."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    day = parse_roc_date(payload.get("date"))
    fields = payload.get("fields") or []
    try:
        code_i = fields.index("證券代號")
        out_i = fields.index("發行股數")
        held_i = fields.index("全體外資及陸資持有股數")
        pct_i = fields.index("全體外資及陸資持股比率")
    except ValueError as exc:
        raise ValueError("unexpected MI_QFIIS columns") from exc
    if day is None:
        raise ValueError("MI_QFIIS payload has no date")
    rows: dict[str, ForeignHolding] = {}
    for item in payload.get("data") or []:
        if not isinstance(item, list) or len(item) <= max(code_i, out_i, held_i, pct_i):
            continue
        row = _foreign_row(
            str(item[code_i]),
            day,
            item[out_i],
            item[held_i],
            item[pct_i],
            suffix=".TW",
            source=TWSE_QFIIS_SOURCE,
            available_at=available_at,
        )
        if row:
            rows[row.symbol] = row
    return list(rows.values())


def parse_tpex_qfii(payload: Any, *, available_at: datetime) -> list[ForeignHolding]:
    """TPEx 僑外資及陸資持股比例排行表 (rank, code, name, issued, room, held, ...)."""
    if not isinstance(payload, dict) or str(payload.get("stat", "ok")).lower() != "ok":
        return []
    rows: dict[str, ForeignHolding] = {}
    for table in payload.get("tables") or []:
        day = parse_roc_date(table.get("date"))
        if day is None:
            raise ValueError("TPEx qfii table has no date")
        for item in table.get("data") or []:
            if not isinstance(item, list) or len(item) < 8:
                continue
            row = _foreign_row(
                str(item[1]),
                day,
                item[3],
                item[5],
                item[7],
                suffix=".TWO",
                source=TPEX_QFII_SOURCE,
                available_at=available_at,
            )
            if row:
                rows[row.symbol] = row
    return list(rows.values())
