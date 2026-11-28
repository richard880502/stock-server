"""Official TAIFEX (臺灣期貨交易所) derivatives data: fetching and pure parsers.

All endpoints verified live on 2026-11-28; none needs a key. The web query pages offer a
CSV download that accepts a date range (history), encoded **MS950/Big5** with CRLF lines
and a trailing comma on some files:

- ``/cht/3/futContractsDateDown`` (POST ``queryStartDate``/``queryEndDate`` ``YYYY/MM/DD``,
  ``commodityId`` blank for all): 三大法人－區分各期貨契約. Columns: 日期, 商品名稱, 身份別
  (自營商/投信/外資及陸資), then 多方/空方 *交易* 口數 + 契約金額(千元), 多空交易淨額, then
  多方/空方 *未平倉* 口數 + 契約金額(千元), 多空未平倉淨額. Roughly six months of history
  could be downloaded in one request.
- ``/cht/3/pcRatioDown``: 臺指選擇權 Put/Call 比 (賣權/買權 成交量, 未平倉量, ratios in %).
- ``/cht/3/callsAndPutsDateDown`` (``commodityId=TXO``): 三大法人－選擇權買賣權分計.
- ``/cht/3/dlLargeTraderFutDown``: 期貨大額交易人未沖銷部位 for every product; month code
  ``999999`` is all months, ``666666`` the weekly contracts; trader type ``0`` is all large
  traders and ``1`` the 特定法人 subset. TX is stated in TX-equivalent contracts
  (TX + MTX/4 + TMF/20).
- ``/cht/3/futDataDown`` (``down_type=1&commodity_id=all``, at most one month per request):
  期貨每日交易行情 for every futures product, one row per product, delivery month (or
  ``a/b`` spread) and session (一般/盤後). 結算價 and 未沖銷契約數 are only on the 一般 row.
- ``/cht/3/dlOptDataDown`` (``down_type=1&commodity_id=TXO``, one day per request, ~0.7 MB):
  選擇權每日交易行情 with 履約價, 買賣權, 未沖銷契約數, 契約到期日.
- ``/file/taifex/Dailydownload/vix/log2data/YYYYMMnew.txt``: VIX per month, tab separated,
  only about the last four months exist.
- ``/cht/2/stockLists`` (HTML) and OpenAPI ``/v1/SSFAdjustedInfo`` (JSON): stock-futures
  underlyings and the shares per contract.

TAIFEX answers an over-eager client with a 302 to a 404 page for several minutes, so the
client below spaces requests by seconds, not the 0.35 s used for TWSE.

Parsers are pure (text in, typed rows out) and raise ``ValueError`` when the header is not
the expected one, e.g. a block or error page returned as HTML.
"""

from __future__ import annotations

import asyncio
import csv
import html
import io
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from quant_signal.domain.derivatives import (
    FuturesInstitutionalDay,
    FuturesLargeTradersDay,
    OptionsInstitutionalDay,
    OptionsOiLevelDay,
    OptionsPcRatioDay,
    StockFuturesDay,
    VixDay,
)

TAIFEX_BASE = "https://www.taifex.com.tw"
OPENAPI_BASE = "https://openapi.taifex.com.tw/v1"
FUT_INSTITUTIONAL_URL = f"{TAIFEX_BASE}/cht/3/futContractsDateDown"
PC_RATIO_URL = f"{TAIFEX_BASE}/cht/3/pcRatioDown"
CALLS_PUTS_URL = f"{TAIFEX_BASE}/cht/3/callsAndPutsDateDown"
LARGE_TRADERS_URL = f"{TAIFEX_BASE}/cht/3/dlLargeTraderFutDown"
FUT_DAILY_URL = f"{TAIFEX_BASE}/cht/3/futDataDown"
OPT_DAILY_URL = f"{TAIFEX_BASE}/cht/3/dlOptDataDown"
VIX_URL = f"{TAIFEX_BASE}/file/taifex/Dailydownload/vix/log2data/{{month}}new.txt"
STOCK_LISTS_URL = f"{TAIFEX_BASE}/cht/2/stockLists"
SSF_ADJUSTED_URL = f"{OPENAPI_BASE}/SSFAdjustedInfo"

SOURCE = "taifex"
TAIPEI = ZoneInfo("Asia/Taipei")
# Institutional positions appear after ~15:00 (day session closes 13:45); the other
# reports follow. Every row is stamped with the same conservative 18:00 Taipei time,
# like the TWSE after-market reports (``twse._available_at``).
PUBLISHED_AT = time(18, 0)

CONTRACT_NAMES = {"臺股期貨": "TX", "小型臺指期貨": "MTX", "微型臺指期貨": "TMF"}
INVESTORS = {"外資及陸資": "foreign", "外資": "foreign", "投信": "trust", "自營商": "dealer"}
# 1 TX = 4 MTX = 20 TMF (contract multipliers NT$200 / 50 / 10 per index point).
TX_EQUIVALENT_FACTOR = {"TX": 1.0, "MTX": 0.25, "TMF": 0.05}
ALL_MONTHS = {"999999", "999912"}


def available_at(trading_date: date) -> datetime:
    return datetime.combine(trading_date, PUBLISHED_AT, tzinfo=TAIPEI).astimezone(UTC)


def taifex_day(day: date) -> str:
    return day.strftime("%Y/%m/%d")


def range_form(start: date, end: date, **extra: str) -> dict[str, str]:
    return {"queryStartDate": taifex_day(start), "queryEndDate": taifex_day(end), **extra}


def fut_institutional_form(start: date, end: date) -> dict[str, str]:
    return range_form(start, end, commodityId="")


def calls_puts_form(start: date, end: date) -> dict[str, str]:
    return range_form(start, end, commodityId="TXO")


def fut_daily_form(start: date, end: date) -> dict[str, str]:
    return range_form(start, end, down_type="1", commodity_id="all")


def opt_daily_form(day: date) -> dict[str, str]:
    return range_form(day, day, down_type="1", commodity_id="TXO", commodity_id2="")


def vix_url(day: date) -> str:
    return VIX_URL.format(month=day.strftime("%Y%m"))


# --- decoding and cell helpers ----------------------------------------------------------


def decode(content: bytes) -> str:
    """Downloads are MS950 (Big5 + HKSCS-ish); strict UTF-8 first for the OpenAPI/BOM files."""
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp950", errors="replace")


def _int(cell: str) -> int | None:
    text = cell.strip().replace(",", "")
    if not text or text in {"-", "--", "NULL"}:
        return None
    try:
        return int(text)
    except ValueError:
        try:
            value = float(text)
        except ValueError:
            return None
        return int(value) if value == int(value) else None


def _float(cell: str) -> float | None:
    text = cell.strip().replace(",", "")
    if not text or text in {"-", "--", "NULL"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _day(cell: str) -> date | None:
    text = cell.strip()
    try:
        if "/" in text:
            year, month, day = text.split("/")
            return date(int(year), int(month), int(day))
        if re.fullmatch(r"\d{8}", text):
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        pass
    return None


def _rows(text: str, *, required: tuple[str, ...]) -> list[list[str]]:
    """CSV rows after the header; the header must contain every ``required`` label."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows or not all(any(label in cell for cell in rows[0]) for label in required):
        raise ValueError(f"unexpected TAIFEX header: {rows[0][:4] if rows else 'empty body'}")
    return [row for row in rows[1:] if row and any(cell.strip() for cell in row)]


# --- 1. institutional positions in index futures ------------------------------------------


def parse_futures_institutional(text: str) -> list[FuturesInstitutionalDay]:
    """TX / MTX / TMF rows of 三大法人－區分各期貨契約; other products are ignored."""
    out: list[FuturesInstitutionalDay] = []
    for row in _rows(text, required=("商品名稱", "身份別", "多方未平倉口數")):
        day = _day(row[0]) if row else None
        contract = CONTRACT_NAMES.get(row[1].strip()) if len(row) > 1 else None
        investor = INVESTORS.get(row[2].strip()) if len(row) > 2 else None
        if day is None or contract is None or investor is None or len(row) < 15:
            continue
        values = [_int(row[i]) for i in (3, 4, 5, 6, 9, 10, 11, 12)]
        if any(value is None for value in values):
            continue
        lt, lv, st, sv, lo, lov, so, sov = values
        out.append(
            FuturesInstitutionalDay(
                trading_date=day,
                contract=contract,  # type: ignore[arg-type]
                investor=investor,  # type: ignore[arg-type]
                long_trade_contracts=lt,  # type: ignore[arg-type]
                long_trade_value_ntd_k=lv,  # type: ignore[arg-type]
                short_trade_contracts=st,  # type: ignore[arg-type]
                short_trade_value_ntd_k=sv,  # type: ignore[arg-type]
                long_oi_contracts=lo,  # type: ignore[arg-type]
                long_oi_value_ntd_k=lov,  # type: ignore[arg-type]
                short_oi_contracts=so,  # type: ignore[arg-type]
                short_oi_value_ntd_k=sov,  # type: ignore[arg-type]
                source=SOURCE,
                available_at=available_at(day),
            )
        )
    return out


# --- 2. options positioning ---------------------------------------------------------------


def parse_pc_ratio(text: str) -> list[OptionsPcRatioDay]:
    out: list[OptionsPcRatioDay] = []
    for row in _rows(text, required=("賣權成交量", "買賣權未平倉量比率")):
        day = _day(row[0])
        if day is None or len(row) < 7:
            continue
        put_v, call_v, put_oi, call_oi = (_int(row[i]) for i in (1, 2, 4, 5))
        vol_ratio, oi_ratio = _float(row[3]), _float(row[6])
        if None in (put_v, call_v, put_oi, call_oi, vol_ratio, oi_ratio):
            continue
        out.append(
            OptionsPcRatioDay(
                trading_date=day,
                put_volume_contracts=put_v,  # type: ignore[arg-type]
                call_volume_contracts=call_v,  # type: ignore[arg-type]
                volume_ratio_pct=vol_ratio,  # type: ignore[arg-type]
                put_oi_contracts=put_oi,  # type: ignore[arg-type]
                call_oi_contracts=call_oi,  # type: ignore[arg-type]
                oi_ratio_pct=oi_ratio,  # type: ignore[arg-type]
                source=SOURCE,
                available_at=available_at(day),
            )
        )
    return out


def parse_options_institutional(text: str) -> list[OptionsInstitutionalDay]:
    """TXO (臺指選擇權) rows of 三大法人－選擇權買賣權分計."""
    out: list[OptionsInstitutionalDay] = []
    for row in _rows(text, required=("買賣權別", "身份別", "買方未平倉口數")):
        day = _day(row[0])
        side = row[2].strip().upper() if len(row) > 2 else ""
        investor = INVESTORS.get(row[3].strip()) if len(row) > 3 else None
        if (
            day is None
            or row[1].strip() != "臺指選擇權"
            or side not in {"CALL", "PUT"}
            or investor is None
            or len(row) < 16
        ):
            continue
        values = [_int(row[i]) for i in (4, 5, 6, 7, 10, 11, 12, 13)]
        if any(value is None for value in values):
            continue
        bt, bv, st, sv, bo, bov, so, sov = values
        out.append(
            OptionsInstitutionalDay(
                trading_date=day,
                contract="TXO",
                call_put=side,  # type: ignore[arg-type]
                investor=investor,  # type: ignore[arg-type]
                buy_trade_contracts=bt,  # type: ignore[arg-type]
                buy_trade_value_ntd_k=bv,  # type: ignore[arg-type]
                sell_trade_contracts=st,  # type: ignore[arg-type]
                sell_trade_value_ntd_k=sv,  # type: ignore[arg-type]
                buy_oi_contracts=bo,  # type: ignore[arg-type]
                buy_oi_value_ntd_k=bov,  # type: ignore[arg-type]
                sell_oi_contracts=so,  # type: ignore[arg-type]
                sell_oi_value_ntd_k=sov,  # type: ignore[arg-type]
                source=SOURCE,
                available_at=available_at(day),
            )
        )
    return out


@dataclass(frozen=True)
class OptionRow:
    trading_date: date
    expiry: str
    strike: float
    call_put: str
    open_interest: int
    expiry_date: date


def parse_options_daily(text: str) -> list[OptionRow]:
    """Regular-session TXO rows (the open-interest column is only filled there)."""
    out: list[OptionRow] = []
    for row in _rows(text, required=("履約價", "未沖銷契約數", "契約到期日")):
        if len(row) < 21 or row[1].strip() != "TXO" or row[17].strip() != "一般":
            continue
        day, expiry_day = _day(row[0]), _day(row[20])
        strike, oi = _float(row[3]), _int(row[11])
        side = {"買權": "CALL", "賣權": "PUT"}.get(row[4].strip())
        if day is None or expiry_day is None or strike is None or oi is None or side is None:
            continue
        out.append(OptionRow(day, row[2].strip(), strike, side, oi, expiry_day))
    return out


def options_oi_levels(rows: list[OptionRow]) -> list[OptionsOiLevelDay]:
    """Nearest monthly and nearest weekly TXO expiry: strikes with the largest open interest.

    Only expiries after the trading date count (the contract expiring that day settles at
    the close). Monthly codes are ``YYYYMM``; weeklies carry a ``W``/``F`` suffix.
    """
    if not rows:
        return []
    day = rows[0].trading_date
    by_expiry: dict[str, list[OptionRow]] = defaultdict(list)
    for row in rows:
        if row.trading_date == day and row.expiry_date > day:
            by_expiry[row.expiry].append(row)
    chosen: dict[str, str] = {}
    for kind in ("monthly", "weekly"):
        candidates = [
            (items[0].expiry_date, code)
            for code, items in by_expiry.items()
            if bool(re.fullmatch(r"\d{6}", code)) == (kind == "monthly")
        ]
        if candidates:
            chosen[kind] = min(candidates)[1]
    out: list[OptionsOiLevelDay] = []
    for kind, code in chosen.items():
        items = by_expiry[code]
        calls = [item for item in items if item.call_put == "CALL"]
        puts = [item for item in items if item.call_put == "PUT"]
        top_call = max(calls, key=lambda r: (r.open_interest, -r.strike), default=None)
        top_put = max(puts, key=lambda r: (r.open_interest, r.strike), default=None)
        out.append(
            OptionsOiLevelDay(
                trading_date=day,
                expiry=code,
                kind=kind,  # type: ignore[arg-type]
                expiry_date=items[0].expiry_date,
                max_call_strike=top_call.strike if top_call and top_call.open_interest else None,
                max_call_oi_contracts=top_call.open_interest if top_call else None,
                max_put_strike=top_put.strike if top_put and top_put.open_interest else None,
                max_put_oi_contracts=top_put.open_interest if top_put else None,
                call_total_oi_contracts=sum(item.open_interest for item in calls),
                put_total_oi_contracts=sum(item.open_interest for item in puts),
                source=SOURCE,
                available_at=available_at(day),
            )
        )
    return out


# --- 3. large traders ---------------------------------------------------------------------


def parse_large_traders(text: str) -> list[FuturesLargeTradersDay]:
    """TX rows (all delivery months combined) of 期貨大額交易人未沖銷部位結構."""
    seen: dict[tuple[date, str], FuturesLargeTradersDay] = {}
    for row in _rows(text, required=("前五大交易人買方", "全市場未沖銷部位數")):
        if len(row) < 10 or row[1].strip() != "TX" or row[3].strip() not in ALL_MONTHS:
            continue
        day = _day(row[0])
        trader_type = {"0": "all", "1": "specific"}.get(row[4].strip())
        values = [_int(row[i]) for i in (5, 6, 7, 8, 9)]
        if day is None or trader_type is None or any(value is None for value in values):
            continue
        t5l, t5s, t10l, t10s, market = values
        seen[(day, trader_type)] = FuturesLargeTradersDay(
            trading_date=day,
            contract="TX",
            trader_type=trader_type,  # type: ignore[arg-type]
            top5_long_contracts=t5l,  # type: ignore[arg-type]
            top5_short_contracts=t5s,  # type: ignore[arg-type]
            top10_long_contracts=t10l,  # type: ignore[arg-type]
            top10_short_contracts=t10s,  # type: ignore[arg-type]
            market_oi_contracts=market,  # type: ignore[arg-type]
            source=SOURCE,
            available_at=available_at(day),
        )
    return list(seen.values())


# --- 4. single-stock futures --------------------------------------------------------------


@dataclass(frozen=True)
class ContractSpec:
    """One stock-futures product: its underlying and the shares (or ETF units) per contract."""

    code: str  # product code as in the daily report, e.g. CDF (adjusted ones end in 1)
    underlying_code: str
    symbol: str  # market_stock_daily form: 2330.TW / 6488.TWO
    shares_per_contract: float
    adjusted: bool = False


def _cells(row_html: str) -> list[str]:
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)
    return [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip() for c in cells]


def _venue_suffix(listed: bool, otc: bool, etf_listed: bool, etf_otc: bool) -> str | None:
    if listed or etf_listed:
        return ".TW"
    if otc or etf_otc:
        return ".TWO"
    return None


def parse_stock_lists(page: str) -> list[ContractSpec]:
    """Stock-futures products from the official underlyings table (標的證券).

    Column 0 is the two-letter product code (the daily report uses it plus ``F``), 2 the
    security code, 4 the 股票期貨標的 flag, 7-10 the venue flags (上市/上櫃 普通股, 上市/上櫃
    ETF) and 11 the standard contract size (2,000 shares, 100 for 小型, 10,000/1,000 units
    for ETFs). The ``◎`` marker is the venue, ``●`` the futures flag.
    """
    out: list[ContractSpec] = []
    for row_html in re.findall(r"<tr>.*?</tr>", page, re.S):
        cells = _cells(row_html)
        if len(cells) < 12 or not re.fullmatch(r"[A-Z0-9]{2}", cells[0]):
            continue
        if "股票期貨標的" not in cells[4]:
            continue
        suffix = _venue_suffix(*(bool(cells[i]) for i in (7, 8, 9, 10)))
        size = _float(cells[11])
        code = cells[2]
        if suffix is None or size is None or not code:
            continue
        out.append(ContractSpec(cells[0] + "F", code, f"{code}{suffix}", size))
    return out


def parse_ssf_adjusted(payload: str) -> list[ContractSpec]:
    """調整型契約 (codes ending in ``1``) with their adjusted shares per contract."""
    out: list[ContractSpec] = []
    for item in json.loads(payload):
        if item.get("Futures/Options") != "股票期貨":
            continue
        venue = str(item.get("TWSEStock/TPExStock/TWSEETF", ""))
        suffix = ".TWO" if "上櫃" in venue else ".TW" if "上市" in venue else None
        size = _float(str(item.get("UnderlyingSecurityShares", "")))
        code = str(item.get("StockCode", "")).strip()
        contract = str(item.get("Contract", "")).strip()
        if suffix and size and code and contract:
            out.append(ContractSpec(contract, code, f"{code}{suffix}", size, adjusted=True))
    return out


@dataclass(frozen=True)
class ContractMonth:
    """One product and delivery month on one date, both sessions merged."""

    trading_date: date
    code: str
    month: str
    volume: int
    settlement: float | None
    open_interest: int | None
    # False for a month that only has after-hours rows (no 一般 row, so no OI to expect).
    regular_session: bool = True


def parse_futures_daily(text: str, codes: set[str]) -> list[ContractMonth]:
    """Daily futures report rows for ``codes``; spreads (``a/b`` months) are dropped.

    Volume adds the 一般 and 盤後 sessions (TAIFEX books an after-hours session under the
    next trading date); 結算價 and 未沖銷契約數 come from the 一般 row.
    """
    merged: dict[tuple[date, str, str], dict] = {}
    for row in _rows(text, required=("到期月份", "未沖銷契約數", "交易時段")):
        if len(row) < 18:
            continue
        code, month = row[1].strip(), row[2].strip()
        day = _day(row[0])
        if code not in codes or day is None or "/" in month or not re.fullmatch(r"\d{6}", month):
            continue
        item = merged.setdefault(
            (day, code, month),
            {"volume": 0, "settlement": None, "oi": None, "regular": False},
        )
        item["volume"] += _int(row[9]) or 0
        if row[17].strip() == "一般":
            item["regular"] = True
            item["settlement"] = _float(row[10])
            item["oi"] = _int(row[11])
    return [
        ContractMonth(day, code, month, v["volume"], v["settlement"], v["oi"], v["regular"])
        for (day, code, month), v in sorted(merged.items())
    ]


def third_wednesday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(2 - first.weekday()) % 7 + 14)


def delivery_date(month: str) -> date:
    """Final settlement day of a stock-futures month: its third Wednesday (holidays ignored)."""
    return third_wednesday(int(month[:4]), int(month[4:6]))


def build_stock_futures_days(
    months: list[ContractMonth], specs: dict[str, ContractSpec]
) -> list[StockFuturesDay]:
    """Aggregate products per underlying (see ``StockFuturesDay`` for the units)."""
    standard: dict[str, float] = {}
    for spec in specs.values():
        if not spec.adjusted:
            standard[spec.symbol] = max(standard.get(spec.symbol, 0.0), spec.shares_per_contract)
    groups: dict[tuple[str, date], list[ContractMonth]] = defaultdict(list)
    for item in months:
        spec = specs.get(item.code)
        if spec is not None:
            groups[(spec.symbol, item.trading_date)].append(item)
    out: list[StockFuturesDay] = []
    for (symbol, day), items in sorted(groups.items()):
        unit = standard.get(symbol) or max(specs[i.code].shares_per_contract for i in items)
        # A regular-session row whose open interest is still "-" makes the total unknown
        # (None), never a smaller number or zero.
        oi_known = all(i.open_interest is not None for i in items if i.regular_session)
        oi_shares = (
            sum(int((i.open_interest or 0) * specs[i.code].shares_per_contract) for i in items)
            if oi_known
            else None
        )
        volume_shares = sum(int(i.volume * specs[i.code].shares_per_contract) for i in items)
        near_month = near_settlement = near_volume = near_oi = None
        candidates = sorted(
            {i.month for i in items if i.settlement is not None and delivery_date(i.month) > day}
        )
        if candidates:
            near_month = candidates[0]
            near_items = [i for i in items if i.month == near_month]
            priced = [i for i in near_items if i.settlement is not None]
            # The standard-size product is the reference price; fall back to any priced one.
            priced.sort(key=lambda i: -specs[i.code].shares_per_contract)
            near_settlement = priced[0].settlement
            near_volume = (
                sum(i.volume * specs[i.code].shares_per_contract for i in near_items) / unit
            )
            if all(i.open_interest is not None for i in near_items if i.regular_session):
                near_oi = (
                    sum(
                        (i.open_interest or 0) * specs[i.code].shares_per_contract
                        for i in near_items
                    )
                    / unit
                )
        out.append(
            StockFuturesDay(
                symbol=symbol,
                trading_date=day,
                underlying_code=specs[items[0].code].underlying_code,
                total_open_interest=None if oi_shares is None else oi_shares / unit,
                total_volume=volume_shares / unit,
                open_interest_shares=oi_shares,
                volume_shares=volume_shares,
                near_month=near_month,
                near_settlement=near_settlement,
                near_volume=near_volume,
                near_open_interest=near_oi,
                product_count=len({i.code for i in items}),
                source=SOURCE,
                available_at=available_at(day),
            )
        )
    return out


# --- 5. VIX -------------------------------------------------------------------------------


def parse_vix(text: str) -> list[VixDay]:
    """One month of ``YYYYMMnew.txt``: date, time, VIX at 13:45 and the last-minute average."""
    out: list[VixDay] = []
    seen_header = False
    for line in text.splitlines():
        if "臺指選擇權波動率指數" in line:
            seen_header = True
        parts = line.split("\t")
        day = _day(parts[0]) if re.fullmatch(r"\d{8}", parts[0].strip()) else None
        if day is None:
            continue
        values = [value for value in (_float(p) for p in parts[2:]) if value is not None]
        if values and values[0] > 0:
            out.append(
                VixDay(
                    trading_date=day,
                    vix_close=values[0],
                    vix_last_minute_avg=values[1] if len(values) > 1 else None,
                    source=SOURCE,
                    available_at=available_at(day),
                )
            )
    if not seen_header:
        raise ValueError("unexpected TAIFEX VIX file")
    return out


# --- HTTP client --------------------------------------------------------------------------


class TaifexBlocked(RuntimeError):
    """TAIFEX refused or could not be reached (non-200 / redirect to /404.htm / timeout):
    the caller must stop sending requests for this tick."""


class TaifexClient:
    """Shared, throttled client for TAIFEX downloads (one instance per worker)."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        request_interval_seconds: float = 6.0,
    ) -> None:
        self._owned_client = client is None
        # No redirect following: a 302 to /404.htm means "slow down", not a page to parse.
        self.client = client or httpx.AsyncClient(
            timeout=60,
            follow_redirects=False,
            headers={
                "Accept": "*/*",
                "User-Agent": "quant-signal-server/0.1 (+research; respectful-rate-limit)",
            },
        )
        self.request_interval_seconds = request_interval_seconds
        self._last_request = 0.0

    async def __aenter__(self) -> TaifexClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def _wait_turn(self) -> None:
        loop = asyncio.get_running_loop()
        wait = self._last_request + self.request_interval_seconds - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = loop.time()

    async def fetch(self, url: str, form: dict[str, str] | None = None) -> str:
        """POST ``form`` (or GET without one); raises ``RuntimeError`` on a non-200 answer."""
        await self._wait_turn()
        try:
            response = (
                await self.client.post(url, data=form)
                if form is not None
                else await self.client.get(url)
            )
        except httpx.HTTPError as exc:
            raise TaifexBlocked(f"TAIFEX request failed: {url}: {exc!r}") from exc
        if response.status_code != 200:
            raise TaifexBlocked(
                f"TAIFEX answered {response.status_code} for {url} "
                f"(location={response.headers.get('location')})"
            )
        return decode(response.content)
