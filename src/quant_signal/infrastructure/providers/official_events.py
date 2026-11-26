"""Official structured market data from the TWSE and TPEx OpenAPI (free, no key).

Endpoints (each returns only the *latest* period; history accumulates from the day
we start fetching):

- 重大訊息 material announcements: TWSE ``opendata/t187ap04_L``,
  TPEx ``mopsfin_t187ap04_O``.
- 處置股 disposition: TWSE ``announcement/punish``, TPEx ``tpex_disposal_information``.
- 注意股 attention: TWSE ``announcement/notice`` (+ ``notetrans``, the "accumulating
  towards disposition" list), TPEx ``tpex_trading_warning_information``
  (+ ``tpex_trading_warning_note``).
- 除權息預告 ex-rights schedule: TWSE ``exchangeReport/TWT48U_ALL``,
  TPEx ``tpex_exright_prepost``.
- 月營收 monthly revenue: TWSE ``opendata/t187ap05_L``, TPEx ``mopsfin_t187ap05_O``.

Parsers are pure (payload in, typed rows out) so they are tested on recorded samples.
Only four-digit common-stock codes are kept, matching the industry classification.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from quant_signal.domain.models import MarketEvent, MonthlyRevenue, RiskFlag

TAIPEI = ZoneInfo("Asia/Taipei")
TWSE_API = "https://openapi.twse.com.tw/v1"
TPEX_API = "https://www.tpex.org.tw/openapi/v1"

TWSE_MATERIAL_URL = f"{TWSE_API}/opendata/t187ap04_L"
TPEX_MATERIAL_URL = f"{TPEX_API}/mopsfin_t187ap04_O"
TWSE_PUNISH_URL = f"{TWSE_API}/announcement/punish"
TPEX_DISPOSAL_URL = f"{TPEX_API}/tpex_disposal_information"
TWSE_NOTICE_URL = f"{TWSE_API}/announcement/notice"
TWSE_NOTETRANS_URL = f"{TWSE_API}/announcement/notetrans"
TPEX_WARNING_URL = f"{TPEX_API}/tpex_trading_warning_information"
TPEX_WARNING_NOTE_URL = f"{TPEX_API}/tpex_trading_warning_note"
TWSE_EXRIGHTS_URL = f"{TWSE_API}/exchangeReport/TWT48U_ALL"
TPEX_EXRIGHTS_URL = f"{TPEX_API}/tpex_exright_prepost"
TWSE_REVENUE_URL = f"{TWSE_API}/opendata/t187ap05_L"
TPEX_REVENUE_URL = f"{TPEX_API}/mopsfin_t187ap05_O"

DETAIL_LIMIT = 2000
_COMMON_STOCK = re.compile(r"^\d{4}$")
_NULL_TOKENS = {"", "-", "--", "—", "n/a", "na", "不適用", "無", "null", "none"}


# --- value helpers ----------------------------------------------------------------


def _text(value: Any) -> str:
    """NFKC-normalised, stripped text (full-width digits/punctuation become ASCII)."""
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def _field(item: dict[str, Any], key: str) -> Any:
    """Look a key up ignoring surrounding whitespace (TWSE ships ``"主旨 "``)."""
    if key in item:
        return item[key]
    for name, value in item.items():
        if isinstance(name, str) and name.strip() == key:
            return value
    return None


def parse_number(value: Any) -> float | None:
    """Parse ``"1,234.5"``, ``"１２３"``, ``"12.5%"``; blanks and "不適用"/"-" become None."""
    text = _text(value).replace(",", "").rstrip("%").strip()
    if text.lower() in _NULL_TOKENS:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_roc_date(value: Any) -> date | None:
    """Parse ROC (民國) or Gregorian dates.

    Accepts ``1151125``, ``990101`` (two-digit ROC year), ``115/11/25``,
    ``民國115年11月25日`` and ``20261125``. Anything unparsable is None.
    """
    text = _text(value)
    if text.lower() in _NULL_TOKENS:
        return None
    match = re.search(r"(\d{2,4})\s*[/\-.年]\s*(\d{1,2})\s*[/\-.月]\s*(\d{1,2})", text)
    if match:
        year, month, day = (int(part) for part in match.groups())
    else:
        digits = re.fullmatch(r"\d{6,8}", text)
        if not digits:
            return None
        if len(text) == 8:
            year, month, day = int(text[:4]), int(text[4:6]), int(text[6:])
        else:
            year, month, day = int(text[:-4]), int(text[-4:-2]), int(text[-2:])
    if year < 1911:  # ROC year
        year += 1911
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_roc_time(value: Any) -> time | None:
    """``"63944"`` -> 06:39:44 (TWSE drops the leading zero); ``"093000"`` and ``9:30:00`` too."""
    text = _text(value)
    if ":" in text:
        parts = text.split(":")
        digits = "".join(part.zfill(2) for part in parts[:3])
    else:
        digits = text
    if not digits.isdigit() or not 3 <= len(digits) <= 6:
        return None
    digits = digits.zfill(6)
    try:
        return time(int(digits[:2]), int(digits[2:4]), int(digits[4:]))
    except ValueError:
        return None


def parse_roc_period(value: Any) -> str | None:
    """``"11510"`` (ROC year + month) -> ``"2026-10"``; ``"202610"`` is accepted too."""
    text = _text(value)
    if not text.isdigit() or len(text) not in (4, 5, 6):
        return None
    if len(text) == 6:
        year, month = int(text[:4]), int(text[4:])
    else:
        year, month = int(text[:-2]) + 1911, int(text[-2:])
    if not 1 <= month <= 12:
        return None
    return f"{year:04d}-{month:02d}"


def _single_line(value: Any) -> str:
    return re.sub(r"\s*[\r\n]+\s*", "", _text(value))


def _truncate(value: str, limit: int = DETAIL_LIMIT) -> str:
    cleaned = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def next_weekday(day: date) -> date:
    following = day + timedelta(days=1)
    while following.weekday() >= 5:
        following += timedelta(days=1)
    return following


def _symbol(code: Any, suffix: str) -> str | None:
    text = _text(code)
    return text + suffix if _COMMON_STOCK.match(text) else None


def _require_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("official payload must be a JSON array")
    return [item for item in payload if isinstance(item, dict)]


# --- 重大訊息 -----------------------------------------------------------------------


def _parse_material(
    payload: Any, *, code_key: str, suffix: str, source: str, available_at: datetime
) -> list[MarketEvent]:
    events: dict[str, MarketEvent] = {}
    for item in _require_list(payload):
        symbol = _symbol(_field(item, code_key), suffix)
        spoken = parse_roc_date(_field(item, "發言日期"))
        title = _single_line(_field(item, "主旨"))
        if symbol is None or spoken is None or not title:
            continue
        spoken_time = parse_roc_time(_field(item, "發言時間")) or time(0, 0)
        announced_at = datetime.combine(spoken, spoken_time, TAIPEI)
        key = hashlib.sha1(f"{symbol}|{announced_at.isoformat()}|{title}".encode()).hexdigest()[:32]
        clause = _text(_field(item, "符合條款")) or None
        detail = _truncate(_text(_field(item, "說明"))) or None
        events[key] = MarketEvent(
            symbol=symbol,
            kind="material",
            external_key=key,
            announced_at=announced_at,
            title=title,
            clause=clause,
            detail=detail,
            source=source,
            available_at=available_at,
        )
    return list(events.values())


def parse_twse_material(payload: Any, *, available_at: datetime) -> list[MarketEvent]:
    return _parse_material(
        payload,
        code_key="公司代號",
        suffix=".TW",
        source="twse_material",
        available_at=available_at,
    )


def parse_tpex_material(payload: Any, *, available_at: datetime) -> list[MarketEvent]:
    return _parse_material(
        payload,
        code_key="SecuritiesCompanyCode",
        suffix=".TWO",
        source="tpex_material",
        available_at=available_at,
    )


# --- 除權息預告 ---------------------------------------------------------------------

_EX_LABEL = {
    "息": "除息",
    "權": "除權",
    "權息": "除權息",
    "除息": "除息",
    "除權": "除權",
    "除權息": "除權息",
}


def _ex_rights_event(
    *,
    symbol: str,
    name: str,
    ex_date: date,
    label: str,
    cash: float | None,
    stock: float | None,
    source: str,
    available_at: datetime,
    extra: list[str],
) -> MarketEvent:
    parts = [f"現金股利 {cash:g} 元" if cash else None, f"股票股利 {stock:g}" if stock else None]
    summary = "、".join(part for part in parts if part)
    title = f"{name} {ex_date.isoformat()} {label}預告" + (f"：{summary}" if summary else "")
    detail = "\n".join([f"除權息日：{ex_date.isoformat()}", f"類別：{label}", *extra])
    return MarketEvent(
        symbol=symbol,
        kind="ex_rights",
        external_key=f"{symbol}:{ex_date.isoformat()}:{label}",
        # A schedule has no announcement time; the first fetch is the first we knew.
        announced_at=available_at,
        title=title,
        clause=label,
        detail=_truncate(detail),
        effective_date=ex_date,
        source=source,
        available_at=available_at,
    )


def _amount_line(label: str, value: float | None) -> list[str]:
    return [f"{label}：{value:g}"] if value else []


def parse_twse_exrights(payload: Any, *, available_at: datetime) -> list[MarketEvent]:
    events: dict[str, MarketEvent] = {}
    for item in _require_list(payload):
        symbol = _symbol(item.get("Code"), ".TW")
        ex_date = parse_roc_date(item.get("Date"))
        label = _EX_LABEL.get(_text(item.get("Exdividend")))
        if symbol is None or ex_date is None or label is None:
            continue
        cash = parse_number(item.get("CashDividend"))
        stock = parse_number(item.get("StockDividendRatio"))
        subscription = parse_number(item.get("SubscriptionRatio"))
        price = parse_number(item.get("SubscriptionPricePerShare"))
        event = _ex_rights_event(
            symbol=symbol,
            name=_text(item.get("Name")),
            ex_date=ex_date,
            label=label,
            cash=cash,
            stock=stock,
            source="twse_exrights",
            available_at=available_at,
            extra=[
                *_amount_line("現金股利(元/股)", cash),
                *_amount_line("股票股利比例", stock),
                *_amount_line("現金增資認購比例", subscription),
                *_amount_line("認購價(元)", price),
            ],
        )
        events[event.external_key] = event
    return list(events.values())


def parse_tpex_exrights(payload: Any, *, available_at: datetime) -> list[MarketEvent]:
    events: dict[str, MarketEvent] = {}
    for item in _require_list(payload):
        symbol = _symbol(item.get("SecuritiesCompanyCode"), ".TWO")
        ex_date = parse_roc_date(item.get("ExRrightsExDividendDate"))
        label = _EX_LABEL.get(_text(item.get("ExRrightsExDividend")))
        if symbol is None or ex_date is None or label is None:
            continue
        cash = parse_number(item.get("CashDividend"))
        stock = parse_number(item.get("StockDividendRatio"))
        subscription = parse_number(item.get("SubscriptionRatioToNewSharesIssued"))
        price = parse_number(item.get("SubscriptionPricePerShare"))  # "註4" -> None
        event = _ex_rights_event(
            symbol=symbol,
            name=_text(item.get("CompanyName")),
            ex_date=ex_date,
            label=label,
            cash=cash,
            stock=stock,
            source="tpex_exrights",
            available_at=available_at,
            extra=[
                *_amount_line("現金股利(元/股)", cash),
                *_amount_line("股票股利比例", stock),
                *_amount_line("現金增資認購比例", subscription),
                *_amount_line("認購價(元)", price),
            ],
        )
        events[event.external_key] = event
    return list(events.values())


# --- 處置股 / 注意股 -----------------------------------------------------------------

_DATE_TOKEN = re.compile(r"\d{2,3}/\d{1,2}/\d{1,2}|\d{7,8}")


def _period_dates(value: Any) -> tuple[date | None, date | None]:
    found = [parse_roc_date(token) for token in _DATE_TOKEN.findall(_text(value))]
    found = [day for day in found if day is not None]
    if not found:
        return None, None
    return found[0], (found[1] if len(found) > 1 else None)


def _disposition(
    *,
    symbol: str | None,
    announced: date | None,
    period: Any,
    reason: str,
    source: str,
    available_at: datetime,
) -> RiskFlag | None:
    start, end = _period_dates(period)
    start = start or announced
    if symbol is None or start is None:
        return None
    return RiskFlag(
        symbol=symbol,
        kind="disposition",
        start_date=start,
        end_date=end,
        reason=_truncate(reason, 500),
        source=source,
        available_at=available_at,
    )


def parse_twse_punish(payload: Any, *, available_at: datetime) -> list[RiskFlag]:
    flags: dict[tuple[str, date, str], RiskFlag] = {}
    for item in _require_list(payload):
        reason = "、".join(
            part
            for part in (
                _single_line(item.get("ReasonsOfDisposition")),
                _single_line(item.get("DispositionMeasures")),
            )
            if part
        )
        flag = _disposition(
            symbol=_symbol(item.get("Code"), ".TW"),
            announced=parse_roc_date(item.get("Date")),
            period=item.get("DispositionPeriod"),
            reason=reason or "處置股",
            source="twse_punish",
            available_at=available_at,
        )
        if flag:
            flags[(flag.symbol, flag.start_date, flag.source)] = flag
    return list(flags.values())


def parse_tpex_disposal(payload: Any, *, available_at: datetime) -> list[RiskFlag]:
    flags: dict[tuple[str, date, str], RiskFlag] = {}
    for item in _require_list(payload):
        flag = _disposition(
            symbol=_symbol(item.get("SecuritiesCompanyCode"), ".TWO"),
            announced=parse_roc_date(item.get("Date")),
            period=item.get("DispositionPeriod"),
            reason=_single_line(item.get("DispositionReasons")) or "處置股",
            source="tpex_disposal",
            available_at=available_at,
        )
        if flag:
            flags[(flag.symbol, flag.start_date, flag.source)] = flag
    return list(flags.values())


def _attention(
    *,
    symbol: str | None,
    start: date | None,
    reason: str,
    source: str,
    available_at: datetime,
) -> RiskFlag | None:
    if symbol is None or start is None:
        return None
    return RiskFlag(
        symbol=symbol,
        kind="attention",
        start_date=start,
        # Announced after the close, it bears on the announcement day and the next session.
        end_date=next_weekday(start),
        reason=_truncate(reason, 500),
        source=source,
        available_at=available_at,
    )


def parse_twse_notice(payload: Any, *, available_at: datetime) -> list[RiskFlag]:
    flags: dict[tuple[str, date, str], RiskFlag] = {}
    for item in _require_list(payload):
        flag = _attention(
            symbol=_symbol(item.get("Code"), ".TW"),
            start=parse_roc_date(item.get("Date")),
            reason=_single_line(item.get("TradingInfoForAttention")) or "注意股",
            source="twse_notice",
            available_at=available_at,
        )
        if flag:
            flags[(flag.symbol, flag.start_date, flag.source)] = flag
    return list(flags.values())


def parse_twse_notetrans(payload: Any, *, available_at: datetime, as_of: date) -> list[RiskFlag]:
    """TWSE 近期累計達注意標準 has no date column: it is a snapshot of ``as_of``."""
    flags: dict[tuple[str, date, str], RiskFlag] = {}
    for item in _require_list(payload):
        criteria = _single_line(item.get("RecentlyMetAttentionSecuritiesCriteria"))
        flag = _attention(
            symbol=_symbol(item.get("Code"), ".TW"),
            start=as_of,
            reason=f"累計達注意標準：{criteria}" if criteria else "累計達注意標準",
            source="twse_notetrans",
            available_at=available_at,
        )
        if flag:
            flags[(flag.symbol, flag.start_date, flag.source)] = flag
    return list(flags.values())


def parse_tpex_warning(payload: Any, *, available_at: datetime) -> list[RiskFlag]:
    flags: dict[tuple[str, date, str], RiskFlag] = {}
    for item in _require_list(payload):
        flag = _attention(
            symbol=_symbol(item.get("SecuritiesCompanyCode"), ".TWO"),
            start=parse_roc_date(item.get("Date")),
            reason=_single_line(item.get("TradingInformation")) or "注意股",
            source="tpex_warning",
            available_at=available_at,
        )
        if flag:
            flags[(flag.symbol, flag.start_date, flag.source)] = flag
    return list(flags.values())


def parse_tpex_warning_note(payload: Any, *, available_at: datetime) -> list[RiskFlag]:
    flags: dict[tuple[str, date, str], RiskFlag] = {}
    for item in _require_list(payload):
        situation = _single_line(item.get("AccumulationSituation"))
        flag = _attention(
            symbol=_symbol(item.get("SecuritiesCompanyCode"), ".TWO"),
            start=parse_roc_date(item.get("Date")),  # note: 8-digit Gregorian here
            reason=f"累計達注意標準：{situation}" if situation else "累計達注意標準",
            source="tpex_warning_note",
            available_at=available_at,
        )
        if flag:
            flags[(flag.symbol, flag.start_date, flag.source)] = flag
    return list(flags.values())


# --- 月營收 -------------------------------------------------------------------------


def _parse_revenue(
    payload: Any, *, suffix: str, source: str, available_at: datetime
) -> list[MonthlyRevenue]:
    rows: dict[tuple[str, str], MonthlyRevenue] = {}
    for item in _require_list(payload):
        symbol = _symbol(_field(item, "公司代號"), suffix)
        period = parse_roc_period(_field(item, "資料年月"))
        revenue = parse_number(_field(item, "營業收入-當月營收"))
        if symbol is None or period is None or revenue is None:
            continue
        rows[(symbol, period)] = MonthlyRevenue(
            symbol=symbol,
            period=period,
            revenue=revenue,
            mom_pct=parse_number(_field(item, "營業收入-上月比較增減(%)")),
            yoy_pct=parse_number(_field(item, "營業收入-去年同月增減(%)")),
            cumulative_revenue=parse_number(_field(item, "累計營業收入-當月累計營收")),
            cumulative_yoy_pct=parse_number(_field(item, "累計營業收入-前期比較增減(%)")),
            source=source,
            available_at=available_at,
        )
    return list(rows.values())


def parse_twse_revenue(payload: Any, *, available_at: datetime) -> list[MonthlyRevenue]:
    return _parse_revenue(payload, suffix=".TW", source="twse_revenue", available_at=available_at)


def parse_tpex_revenue(payload: Any, *, available_at: datetime) -> list[MonthlyRevenue]:
    return _parse_revenue(payload, suffix=".TWO", source="tpex_revenue", available_at=available_at)
