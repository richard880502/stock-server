"""Whole-market multi-factor screener: pure, deterministic math (no I/O).

Per stock, :func:`compute_metrics` turns the price/flow/credit history up to a date into a
flat dict of metrics. Across the whole universe, :func:`score_universe` turns those metrics
into 0-100 cross-sectional percentile scores per factor family plus a composite, and
:func:`build_highlights` picks short factual Traditional-Chinese call-outs. Nothing here
recommends a trade: scores are rankings of historical data, nothing more.

Conventions
-----------
* Daily return ``r_t = change / (close - change)`` (the exchange-reported change already
  removes ex-dividend gaps). The adjusted price series is chained backwards from the last
  close with those returns, so moving averages / highs are not distorted by dividends. It
  stops at the first day whose reference price is unknown.
* Institutional flow windows end at ``flow_through`` (the newest session with a published
  report) and only count sessions where the report exists for the stock. Share counts are
  converted to lots (張, 1,000 shares).
* A metric that cannot be computed is ``None`` (never 0).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

if TYPE_CHECKING:
    from quant_signal.domain.models import ForeignHolding, ShareholdingPair

VERSION = "screener_v1"
SHARES_PER_LOT = 1000
MIN_SESSIONS = 20
HISTORY_SESSIONS = 260
TRADING_DAYS_PER_YEAR = 252
HIGH_WINDOW = 250

HOLDER_KEYS = (
    "big_holder_pct",
    "whale_pct",
    "big_holder_pct_change",
    "whale_pct_change",
    "retail_holders_change_pct",
    "holders_weeks_span",
    "holders_data_date",
    "foreign_holding_pct",
)

# --------------------------------------------------------------------- definitions

Transform = Literal["id", "neg", "neg_abs"]

# Factor family -> ordered (metric key, weight, transform). The single place weights live.
FAMILY_INPUTS: dict[str, list[tuple[str, float, Transform]]] = {
    "momentum": [
        ("ret_20d", 0.30, "id"),
        ("ret_60d", 0.30, "id"),
        ("ret_120d", 0.20, "id"),
        ("high_52w_gap_pct", 0.20, "id"),
    ],
    "trend": [
        ("ma20_gap_pct", 0.30, "id"),
        ("ma60_gap_pct", 0.40, "id"),
        ("ma120_gap_pct", 0.30, "id"),
    ],
    "flow": [
        ("inst_flow_ratio_20d", 0.255, "id"),
        ("inst_flow_ratio_60d", 0.2125, "id"),
        ("inst_buy_days_20d", 0.1275, "id"),
        ("cmf_20", 0.1275, "id"),
        ("margin_change_20d_pct", 0.1275, "neg"),
        ("inst_flow_to_shares_20d", 0.15, "id"),
    ],
    "accumulation": [
        ("inst_flow_ratio_60d", 0.2925, "id"),
        ("inst_buy_days_60d", 0.195, "id"),
        ("ret_20d", 0.1625, "neg_abs"),
        ("big_holder_pct_change", 0.25, "id"),
        ("retail_holders_change_pct", 0.10, "neg"),
    ],
    "fundamental": [
        ("revenue_yoy_pct", 0.60, "id"),
        ("revenue_cum_yoy_pct", 0.40, "id"),
    ],
    "stability": [
        ("volatility_20d", 0.50, "neg"),
        ("max_drawdown_60d", 0.50, "id"),
    ],
}
COMPOSITE_WEIGHTS: dict[str, float] = {
    "momentum": 0.25,
    "trend": 0.15,
    "flow": 0.30,
    "fundamental": 0.15,
    "stability": 0.15,
}
SCORE_KEYS = tuple(FAMILY_INPUTS)


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    group: str
    unit: str
    decimals: int
    description: str
    higher_is_better: bool | None
    kind: Literal["number", "bool", "text"] = "number"


def _m(
    key: str,
    label: str,
    group: str,
    unit: str,
    decimals: int,
    description: str,
    better: bool | None = True,
    kind: Literal["number", "bool", "text"] = "number",
) -> MetricDef:
    return MetricDef(key, label, group, unit, decimals, description, better, kind)


METRICS: tuple[MetricDef, ...] = (
    _m("change_pct", "單日漲跌幅", "price", "%", 2, "最新交易日相對參考價的漲跌幅", None),
    _m("ret_5d", "5 日報酬", "price", "%", 1, "近 5 個交易日還原報酬"),
    _m("ret_20d", "20 日報酬", "price", "%", 1, "近 20 個交易日還原報酬"),
    _m("ret_60d", "60 日報酬", "price", "%", 1, "近 60 個交易日還原報酬"),
    _m("ret_120d", "120 日報酬", "price", "%", 1, "近 120 個交易日還原報酬"),
    _m("ma20_gap_pct", "距 20 日均線", "price", "%", 1, "還原收盤價相對 20 日均線的乖離"),
    _m("ma60_gap_pct", "距 60 日均線", "price", "%", 1, "還原收盤價相對 60 日均線的乖離"),
    _m("ma120_gap_pct", "距 120 日均線", "price", "%", 1, "還原收盤價相對 120 日均線的乖離"),
    _m(
        "high_52w_gap_pct",
        "距 52 週高點",
        "price",
        "%",
        1,
        "還原收盤價相對近 250 個交易日最高價的差距（不大於 0）",
    ),
    _m(
        "trend_aligned",
        "均線多頭排列",
        "price",
        "",
        0,
        "收盤價 > 20 日均線 > 60 日均線 > 120 日均線",
        True,
        "bool",
    ),
    _m("avg_turnover_20d", "20 日均成交值", "liquidity", "元", 0, "近 20 日平均成交金額", None),
    _m(
        "volume_ratio_5_60",
        "量比 (5/60)",
        "liquidity",
        "倍",
        2,
        "近 5 日均量除以近 60 日均量",
        None,
    ),
    _m(
        "market_cap",
        "市值",
        "liquidity",
        "元",
        0,
        "流通在外股數乘以最新收盤價（股數取自外資持股統計）",
        None,
    ),
    _m(
        "turnover_rate_20d",
        "20 日週轉率",
        "liquidity",
        "%",
        1,
        "近 20 日成交股數合計除以流通在外股數",
        None,
    ),
    _m("sessions", "資料天數", "liquidity", "日", 0, "用於計算的歷史交易日數", None),
    _m("volatility_20d", "20 日波動率", "risk", "%", 1, "近 20 日日報酬標準差年化", False),
    _m("max_drawdown_60d", "60 日最大回檔", "risk", "%", 1, "近 60 日還原價相對前高的最大跌幅"),
    _m("foreign_net_5d", "外資 5 日買賣超", "flow", "張", 0, "外資近 5 個有資料日淨買超"),
    _m("foreign_net_20d", "外資 20 日買賣超", "flow", "張", 0, "外資近 20 個有資料日淨買超"),
    _m("foreign_net_60d", "外資 60 日買賣超", "flow", "張", 0, "外資近 60 個有資料日淨買超"),
    _m("trust_net_5d", "投信 5 日買賣超", "flow", "張", 0, "投信近 5 個有資料日淨買超"),
    _m("trust_net_20d", "投信 20 日買賣超", "flow", "張", 0, "投信近 20 個有資料日淨買超"),
    _m("trust_net_60d", "投信 60 日買賣超", "flow", "張", 0, "投信近 60 個有資料日淨買超"),
    _m("dealer_net_20d", "自營商 20 日買賣超", "flow", "張", 0, "自營商近 20 個有資料日淨買超"),
    _m("total_net_5d", "法人 5 日買賣超", "flow", "張", 0, "三大法人近 5 個有資料日合計淨買超"),
    _m("total_net_20d", "法人 20 日買賣超", "flow", "張", 0, "三大法人近 20 個有資料日合計淨買超"),
    _m("total_net_60d", "法人 60 日買賣超", "flow", "張", 0, "三大法人近 60 個有資料日合計淨買超"),
    _m(
        "inst_flow_ratio_20d",
        "法人 20 日買超佔成交量",
        "flow",
        "%",
        1,
        "三大法人 20 日淨買超除以同期成交量",
    ),
    _m(
        "inst_flow_ratio_60d",
        "法人 60 日買超佔成交量",
        "flow",
        "%",
        1,
        "三大法人 60 日淨買超除以同期成交量",
    ),
    _m(
        "foreign_flow_ratio_20d",
        "外資 20 日買超佔成交量",
        "flow",
        "%",
        1,
        "外資 20 日淨買超除以同期成交量",
    ),
    _m(
        "trust_flow_ratio_20d",
        "投信 20 日買超佔成交量",
        "flow",
        "%",
        1,
        "投信 20 日淨買超除以同期成交量",
    ),
    _m("foreign_streak", "外資連買賣日數", "flow", "日", 0, "外資連續買超（正）或賣超（負）的日數"),
    _m("trust_streak", "投信連買賣日數", "flow", "日", 0, "投信連續買超（正）或賣超（負）的日數"),
    _m(
        "inst_buy_days_20d",
        "法人 20 日買超日占比",
        "flow",
        "%",
        0,
        "近 20 個有資料日中法人買超的比例",
    ),
    _m(
        "inst_buy_days_60d",
        "法人 60 日買超日占比",
        "flow",
        "%",
        0,
        "近 60 個有資料日中法人買超的比例",
    ),
    _m(
        "inst_flow_to_shares_20d",
        "法人 20 日買超佔股本",
        "flow",
        "%",
        2,
        "三大法人 20 日淨買超股數除以流通在外股數",
    ),
    _m(
        "inst_flow_to_shares_60d",
        "法人 60 日買超佔股本",
        "flow",
        "%",
        2,
        "三大法人 60 日淨買超股數除以流通在外股數",
    ),
    _m("cmf_20", "20 日 CMF 資金流", "flow", "", 2, "Chaikin Money Flow，-1 到 1，由價量推算"),
    _m(
        "up_volume_ratio_20d",
        "20 日上漲日成交量占比",
        "flow",
        "%",
        1,
        "近 20 日上漲日成交量佔總成交量",
    ),
    _m(
        "margin_change_20d_pct",
        "融資 20 日增減",
        "credit",
        "%",
        1,
        "融資餘額 20 日變化佔期初餘額",
        False,
    ),
    _m(
        "short_change_20d_pct",
        "融券 20 日增減",
        "credit",
        "%",
        1,
        "融券餘額 20 日變化佔期初餘額",
        None,
    ),
    _m(
        "chip_transfer_20d",
        "法人買、融資退",
        "credit",
        "",
        0,
        "法人 20 日淨買超且融資餘額減少（籌碼由散戶轉向法人）",
        True,
        "bool",
    ),
    _m("revenue_period", "營收月份", "fundamental", "", 0, "最新一期月營收所屬月份", None, "text"),
    _m("revenue_yoy_pct", "營收年增率", "fundamental", "%", 1, "最新月營收年增率"),
    _m("revenue_mom_pct", "營收月增率", "fundamental", "%", 1, "最新月營收月增率"),
    _m("revenue_cum_yoy_pct", "累計營收年增率", "fundamental", "%", 1, "累計營收年增率"),
    _m(
        "big_holder_pct",
        "400 張以上大戶持股",
        "holders",
        "%",
        2,
        "集保戶股權分散表中持股 400 張以上者的持股比例（週資料）",
        None,
    ),
    _m(
        "whale_pct",
        "1,000 張以上大戶持股",
        "holders",
        "%",
        2,
        "集保戶股權分散表中持股 1,000 張以上者的持股比例（週資料）",
        None,
    ),
    _m(
        "big_holder_pct_change",
        "400 張大戶持股變化",
        "holders",
        "個百分點",
        2,
        "400 張以上大戶持股比例相對比較週的變化",
    ),
    _m(
        "whale_pct_change",
        "1,000 張大戶持股變化",
        "holders",
        "個百分點",
        2,
        "1,000 張以上大戶持股比例相對比較週的變化",
    ),
    _m(
        "retail_holders_change_pct",
        "散戶人數變化",
        "holders",
        "%",
        1,
        "持股 10 張以下的股東人數相對比較週的變化（負值＝散戶人數減少）",
        False,
    ),
    _m(
        "holders_weeks_span",
        "持股比較間隔",
        "holders",
        "日",
        0,
        "兩個集保週資料之間相隔的天數（即上述變化涵蓋的期間）",
        None,
    ),
    _m(
        "holders_data_date",
        "集保資料日",
        "holders",
        "",
        0,
        "所使用的集保股權分散表資料日期",
        None,
        "text",
    ),
    _m(
        "foreign_holding_pct",
        "外資持股比例",
        "holders",
        "%",
        2,
        "外資及陸資持股佔流通在外股數的比例",
        None,
    ),
)
METRIC_BY_KEY = {metric.key: metric for metric in METRICS}
NUMERIC_KEYS = frozenset(m.key for m in METRICS if m.kind == "number")
BOOL_KEYS = frozenset(m.key for m in METRICS if m.kind == "bool")

GROUPS: tuple[tuple[str, str], ...] = (
    ("score", "因子分數"),
    ("price", "價格動能"),
    ("flow", "資金流向"),
    ("holders", "大戶籌碼"),
    ("credit", "信用交易"),
    ("fundamental", "營收"),
    ("risk", "風險"),
    ("liquidity", "流動性"),
)

FACTOR_LABELS: dict[str, tuple[str, str]] = {
    "composite": ("綜合評分", "動能、趨勢、資金流向、營收與穩定度的加權百分位（缺項時重新配權）"),
    "momentum": ("價格動能", "20/60/120 日報酬與距 52 週高點的全市場百分位"),
    "trend": ("趨勢", "股價相對 20/60/120 日均線乖離的全市場百分位"),
    "flow": ("資金流向", "法人買超佔成交量與股本、買超日占比、CMF 與融資變化的全市場百分位"),
    "accumulation": (
        "低調吸籌",
        "法人長期持續買超、大戶增持且散戶減少、但股價尚未大幅波動的程度（百分位；集保資料缺時重新配權）",
    ),
    "fundamental": ("營收", "最新月營收年增率與累計年增率的全市場百分位"),
    "stability": ("穩定度", "低波動與較小回檔的全市場百分位"),
}

# ------------------------------------------------------------------------- input

_NAN = float("nan")


@dataclass(frozen=True)
class StockHistory:
    """One stock's daily history, oldest first. ``nan`` marks an unpublished report."""

    symbol: str
    name: str
    market: str
    dates: list[date]
    close: np.ndarray
    change: np.ndarray
    high: np.ndarray
    low: np.ndarray
    volume: np.ndarray
    turnover: np.ndarray
    foreign: np.ndarray
    trust: np.ndarray
    dealer: np.ndarray
    total: np.ndarray
    margin_balance: np.ndarray
    margin_change: np.ndarray
    short_balance: np.ndarray
    short_change: np.ndarray


def _arr(values: list[Any]) -> np.ndarray:
    return np.array([_NAN if v is None else float(v) for v in values], dtype=np.float64)


def history_from_rows(symbol: str, name: str, market: str, rows: list[tuple]) -> StockHistory:
    """Build a history from ``(date, close, change, high, low, volume, turnover, foreign,
    trust, dealer, total, margin_balance, margin_change, short_balance, short_change)``
    tuples sorted by date."""
    columns = list(zip(*rows, strict=True)) if rows else [[] for _ in range(15)]
    return StockHistory(
        symbol=symbol,
        name=name,
        market=market,
        dates=list(columns[0]),
        close=_arr(columns[1]),
        change=_arr(columns[2]),
        high=_arr(columns[3]),
        low=_arr(columns[4]),
        volume=_arr(columns[5]),
        turnover=_arr(columns[6]),
        foreign=_arr(columns[7]),
        trust=_arr(columns[8]),
        dealer=_arr(columns[9]),
        total=_arr(columns[10]),
        margin_balance=_arr(columns[11]),
        margin_change=_arr(columns[12]),
        short_balance=_arr(columns[13]),
        short_change=_arr(columns[14]),
    )


# -------------------------------------------------------------------- price maths


def daily_returns(close: np.ndarray, change: np.ndarray) -> np.ndarray:
    """``change / (close - change)``; ``nan`` where the reference price is not positive."""
    previous = close - change
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(previous > 0, change / previous, _NAN)


def adjusted_levels(close: np.ndarray, returns: np.ndarray) -> tuple[np.ndarray, float | None]:
    """Back-chain the close series from its last value using the daily returns.

    Returns ``(adjusted closes, reference level)``. The series starts at the newest day
    whose own return is unknown (earlier days cannot be linked). The reference level is
    the adjusted previous close of the first row, when that row's return is known.
    """
    n = len(close)
    unknown = np.flatnonzero(np.isnan(returns[1:])) + 1 if n > 1 else np.array([], dtype=int)
    start = int(unknown[-1]) if unknown.size else 0
    growth = 1.0 + returns[start + 1 :]
    # suffix[j] = prod(growth[j:]); adjusted[start + j] = last_close / suffix[j]
    suffix = np.cumprod(growth[::-1])[::-1]
    adjusted = np.concatenate([close[-1] / suffix, [close[-1]]]) if growth.size else close[-1:]
    reference: float | None = None
    if start == 0 and not math.isnan(returns[0]):
        reference = float(adjusted[0] / (1.0 + returns[0]))
    return adjusted, reference


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return round(float(value), digits)


def _pct_return(levels: np.ndarray, window: int) -> float | None:
    if len(levels) < window + 1:
        return None
    return (levels[-1] / levels[-1 - window] - 1.0) * 100.0


def _gap_to_mean(adjusted: np.ndarray, window: int) -> float | None:
    if len(adjusted) < window:
        return None
    return (adjusted[-1] / float(np.mean(adjusted[-window:])) - 1.0) * 100.0


def _max_drawdown(adjusted: np.ndarray, window: int) -> float | None:
    if len(adjusted) < window:
        return None
    tail = adjusted[-window:]
    return float(np.min(tail / np.maximum.accumulate(tail) - 1.0)) * 100.0


def _volatility(returns: np.ndarray, window: int) -> float | None:
    tail = returns[-window:]
    if len(tail) < window or np.isnan(tail).any():
        return None
    return float(np.std(tail, ddof=1)) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0


def chaikin_money_flow(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray
) -> float | None:
    """Sum of money-flow volume over sum of volume (inputs are the window itself)."""
    total = float(np.sum(volume))
    if total <= 0:
        return None
    span = high - low
    with np.errstate(divide="ignore", invalid="ignore"):
        multiplier = np.where(span > 0, ((close - low) - (high - close)) / span, 0.0)
    return float(np.sum(multiplier * volume)) / total


# --------------------------------------------------------------------- flow maths


def _flow_window(values: np.ndarray, end: int, size: int) -> np.ndarray | None:
    """Slice of ``size`` sessions ending at index ``end`` (inclusive)."""
    start = end + 1 - size
    return values[start : end + 1] if start >= 0 else None


def _flow_sum_lots(values: np.ndarray, end: int, size: int) -> float | None:
    window = _flow_window(values, end, size)
    if window is None:
        return None
    known = ~np.isnan(window)
    if known.sum() < math.ceil(size / 2):
        return None
    return float(np.sum(window[known])) / SHARES_PER_LOT


def _flow_ratio(values: np.ndarray, volume: np.ndarray, end: int, size: int) -> float | None:
    window, vol = _flow_window(values, end, size), _flow_window(volume, end, size)
    if window is None or vol is None:
        return None
    known = ~np.isnan(window)
    if known.sum() < math.ceil(size / 2):
        return None
    traded = float(np.sum(vol[known]))
    return float(np.sum(window[known])) / traded * 100.0 if traded > 0 else None


def _buy_day_share(values: np.ndarray, end: int, size: int) -> float | None:
    window = _flow_window(values, end, size)
    if window is None:
        return None
    known = window[~np.isnan(window)]
    if known.size < math.ceil(size / 2):
        return None
    return float(np.sum(known > 0)) / known.size * 100.0


def _streak(values: np.ndarray, end: int) -> int | None:
    """Consecutive net-buy (positive) / net-sell (negative) sessions back from ``end``."""
    latest = values[end]
    if math.isnan(latest):
        return None
    if latest == 0:
        return 0
    sign = 1.0 if latest > 0 else -1.0
    count = 0
    for value in values[end::-1]:
        if math.isnan(value) or value * sign <= 0:
            break
        count += 1
    return int(sign) * count


def _credit_change_pct(
    balance: np.ndarray, change: np.ndarray, last: int, size: int = 20
) -> float | None:
    """Balance change over ``size`` sessions ending at ``last`` as % of the starting balance."""
    if last < size - 1 or math.isnan(balance[last]):
        return None
    window = change[last + 1 - size : last + 1]
    if np.isnan(window).any():
        return None
    start = float(balance[last]) - float(np.sum(window))
    return float(np.sum(window)) / start * 100.0 if start > 0 else None


# ----------------------------------------------------------------- per-stock metrics


def holder_metrics(pair: ShareholdingPair | None, foreign: ForeignHolding | None) -> dict[str, Any]:
    """TDCC concentration and foreign-holding metrics; ``None`` where an input is missing."""
    out: dict[str, Any] = dict.fromkeys(HOLDER_KEYS)
    if pair is not None:
        latest, prior = pair.latest, pair.prior
        out["big_holder_pct"] = latest.pct_ge400
        out["whale_pct"] = latest.pct_ge1000
        out["holders_data_date"] = latest.data_date.isoformat()
        if prior is not None:
            out["big_holder_pct_change"] = latest.pct_ge400 - prior.pct_ge400
            out["whale_pct_change"] = latest.pct_ge1000 - prior.pct_ge1000
            out["holders_weeks_span"] = (latest.data_date - prior.data_date).days
            if prior.retail_holders > 0:
                out["retail_holders_change_pct"] = (
                    (latest.retail_holders - prior.retail_holders) / prior.retail_holders * 100.0
                )
    if foreign is not None:
        out["foreign_holding_pct"] = foreign.foreign_holding_pct
    return out


def compute_metrics(
    history: StockHistory,
    flow_through: date | None,
    holders: ShareholdingPair | None = None,
    foreign: ForeignHolding | None = None,
) -> dict[str, Any] | None:
    """All spec metrics for the newest session in ``history``; ``None`` if under 20 sessions.

    ``flow_through`` is the newest session (≤ the newest date) with institutional data.
    ``holders`` / ``foreign`` are the point-in-time TDCC pair and foreign-holding row.
    """
    n = len(history.dates)
    if n < MIN_SESSIONS:
        return None
    close, volume = history.close, history.volume
    returns = daily_returns(close, history.change)
    adjusted, reference = adjusted_levels(close, returns)
    levels = adjusted if reference is None else np.concatenate([[reference], adjusted])

    m: dict[str, Any] = {}
    last_return = returns[-1]
    m["change_pct"] = None if math.isnan(last_return) else last_return * 100.0
    for window in (5, 20, 60, 120):
        m[f"ret_{window}d"] = _pct_return(levels, window)
    for window in (20, 60, 120):
        m[f"ma{window}_gap_pct"] = _gap_to_mean(adjusted, window)
    if len(adjusted) >= HIGH_WINDOW:
        m["high_52w_gap_pct"] = (adjusted[-1] / float(np.max(adjusted[-HIGH_WINDOW:])) - 1) * 100
    else:
        m["high_52w_gap_pct"] = None
    if len(adjusted) >= 120:
        ma20, ma60, ma120 = (float(np.mean(adjusted[-w:])) for w in (20, 60, 120))
        m["trend_aligned"] = bool(adjusted[-1] > ma20 > ma60 > ma120)
    else:
        m["trend_aligned"] = None

    m["avg_turnover_20d"] = float(np.mean(history.turnover[-20:]))
    avg60 = float(np.mean(volume[-60:])) if n >= 60 else 0.0
    m["volume_ratio_5_60"] = float(np.mean(volume[-5:])) / avg60 if avg60 > 0 else None
    m["sessions"] = n
    m["volatility_20d"] = _volatility(returns, 20)
    m["max_drawdown_60d"] = _max_drawdown(adjusted, 60)

    end = -1
    if flow_through is not None:
        eligible = [i for i, day in enumerate(history.dates) if day <= flow_through]
        end = eligible[-1] if eligible else -1
    flow_keys = (
        "foreign_net_5d foreign_net_20d foreign_net_60d trust_net_5d trust_net_20d trust_net_60d "
        "dealer_net_20d total_net_5d total_net_20d total_net_60d inst_flow_ratio_20d "
        "inst_flow_ratio_60d foreign_flow_ratio_20d trust_flow_ratio_20d foreign_streak "
        "trust_streak inst_buy_days_20d inst_buy_days_60d"
    ).split()
    m.update(dict.fromkeys(flow_keys))
    if end >= 0:
        for who, values in (("foreign", history.foreign), ("trust", history.trust)):
            for window in (5, 20, 60):
                m[f"{who}_net_{window}d"] = _flow_sum_lots(values, end, window)
            m[f"{who}_flow_ratio_20d"] = _flow_ratio(values, volume, end, 20)
            m[f"{who}_streak"] = _streak(values, end)
        m["dealer_net_20d"] = _flow_sum_lots(history.dealer, end, 20)
        for window in (5, 20, 60):
            m[f"total_net_{window}d"] = _flow_sum_lots(history.total, end, window)
        for window in (20, 60):
            m[f"inst_flow_ratio_{window}d"] = _flow_ratio(history.total, volume, end, window)
            m[f"inst_buy_days_{window}d"] = _buy_day_share(history.total, end, window)

    tail = slice(-20, None)
    m["cmf_20"] = chaikin_money_flow(
        history.high[tail], history.low[tail], close[tail], volume[tail]
    )
    traded = float(np.sum(volume[tail]))
    up = float(np.sum(volume[tail][history.change[tail] > 0]))
    m["up_volume_ratio_20d"] = up / traded * 100.0 if traded > 0 else None

    credit_known = np.flatnonzero(~np.isnan(history.margin_balance))
    last_credit = int(credit_known[-1]) if credit_known.size else -1
    margin = short = None
    if last_credit >= 0 and n - 1 - last_credit <= 2:
        margin = _credit_change_pct(history.margin_balance, history.margin_change, last_credit)
        short = _credit_change_pct(history.short_balance, history.short_change, last_credit)
    m["margin_change_20d_pct"] = margin
    m["short_change_20d_pct"] = short
    net20 = m["total_net_20d"]
    m["chip_transfer_20d"] = None if net20 is None or margin is None else bool(net20 > 0 > margin)

    m.update(holder_metrics(holders, foreign))
    shares = foreign.shares_outstanding if foreign is not None else 0
    m["market_cap"] = shares * float(close[-1]) if shares > 0 else None
    m["turnover_rate_20d"] = float(np.sum(volume[tail])) / shares * 100.0 if shares > 0 else None
    for window in (20, 60):
        lots = m[f"total_net_{window}d"]
        m[f"inst_flow_to_shares_{window}d"] = (
            lots * SHARES_PER_LOT / shares * 100.0 if shares > 0 and lots is not None else None
        )

    return {key: (_round(value) if isinstance(value, float) else value) for key, value in m.items()}


# ------------------------------------------------------------------ cross-section


def percentile_ranks(values: list[float | None]) -> list[float | None]:
    """0-100 percentile rank among the non-None values; ties share the average rank.

    ``(average 1-based rank - 1) / (count - 1) * 100``; a single value scores 50.
    """
    known = [(i, v) for i, v in enumerate(values) if v is not None and not math.isnan(v)]
    out: list[float | None] = [None] * len(values)
    if not known:
        return out
    if len(known) == 1:
        out[known[0][0]] = 50.0
        return out
    array = np.array([v for _, v in known], dtype=np.float64)
    unique, inverse, counts = np.unique(array, return_inverse=True, return_counts=True)
    first = np.cumsum(counts) - counts
    average_rank = first + (counts + 1) / 2.0
    scores = (average_rank[inverse] - 1.0) / (len(known) - 1) * 100.0
    for (index, _), score in zip(known, scores, strict=True):
        out[index] = float(score)
    return out


def _transform(value: Any, how: Transform) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if how == "neg":
        return -float(value)
    if how == "neg_abs":
        return -abs(float(value))
    return float(value)


def family_score(percentiles: list[tuple[float | None, float]]) -> float | None:
    """Weighted mean of the available percentiles; ``None`` if they carry under half the weight."""
    available = [(p, w) for p, w in percentiles if p is not None]
    weight = sum(w for _, w in available)
    if not available or weight < sum(w for _, w in percentiles) / 2 - 1e-9:
        return None
    return sum(p * w for p, w in available) / weight


def composite_score(scores: dict[str, float | None]) -> float | None:
    """Weighted mean of the available families; ``None`` if flow and momentum are both None."""
    if scores.get("flow") is None and scores.get("momentum") is None:
        return None
    available = [(scores[k], w) for k, w in COMPOSITE_WEIGHTS.items() if scores.get(k) is not None]
    weight = sum(w for _, w in available)
    return sum(s * w for s, w in available) / weight  # type: ignore[operator]


def score_universe(
    metrics: list[dict[str, Any]],
) -> list[tuple[dict[str, float | None], float | None]]:
    """Per stock: ``(scores by family, composite)`` using percentiles over this universe."""
    percentiles: dict[tuple[str, str], list[float | None]] = {}
    for inputs in FAMILY_INPUTS.values():
        for key, _, how in inputs:
            if (key, how) not in percentiles:
                column = [_transform(item.get(key), how) for item in metrics]
                percentiles[(key, how)] = percentile_ranks(column)
    result: list[tuple[dict[str, float | None], float | None]] = []
    for index in range(len(metrics)):
        scores: dict[str, float | None] = {}
        for family, inputs in FAMILY_INPUTS.items():
            value = family_score([(percentiles[(k, h)][index], w) for k, w, h in inputs])
            scores[family] = _round(value, 2)
        composite = composite_score(scores)
        result.append((scores, _round(composite, 2)))
    return result


# ---------------------------------------------------------------------- highlights


def _num(value: float, digits: int = 1) -> str:
    return f"{abs(value):.{digits}f}"


def build_highlights(metrics: dict[str, Any], flags: list[str], limit: int = 4) -> list[str]:
    """Up to ``limit`` factual call-outs, strongest first (deterministic, no advice)."""
    candidates: list[tuple[float, int, str]] = []

    def add(strength: float, text: str) -> None:
        candidates.append((strength, len(candidates), text))

    def val(key: str) -> Any:
        return metrics.get(key)

    if "disposition" in flags:
        add(1.0, "處置股")
    elif "attention" in flags:
        add(0.7, "注意股")
    if val("chip_transfer_20d") is True:
        add(0.95, "法人買、融資退（籌碼轉強）")
    for key, who in (("trust_streak", "投信"), ("foreign_streak", "外資")):
        streak = val(key)
        if streak is not None and streak >= 3:
            add(0.5 + min(streak, 15) / 30, f"{who}連買 {streak} 日")
        elif streak is not None and streak <= -3:
            add(0.5 + min(-streak, 15) / 30, f"{who}連賣 {-streak} 日")
    ratio = val("inst_flow_ratio_20d")
    if ratio is not None and ratio >= 3:
        add(0.4 + min(ratio, 10) / 20, f"法人 20 日買超佔成交量 {_num(ratio)}%")
    elif ratio is not None and ratio <= -3:
        add(0.4 + min(-ratio, 10) / 20, f"法人 20 日賣超佔成交量 {_num(ratio)}%")
    margin = val("margin_change_20d_pct")
    if margin is not None and margin <= -3:
        add(0.3 + min(-margin, 15) / 30, f"融資 20 日減 {_num(margin)}%")
    elif margin is not None and margin >= 10:
        add(0.3 + min(margin, 30) / 60, f"融資 20 日增 {_num(margin)}%")
    yoy = val("revenue_yoy_pct")
    if yoy is not None and yoy >= 20:
        add(0.3 + min(yoy, 100) / 200, f"營收年增 {_num(yoy)}%")
    elif yoy is not None and yoy <= -20:
        add(0.3 + min(-yoy, 100) / 200, f"營收年減 {_num(yoy)}%")
    gap = val("high_52w_gap_pct")
    if gap is not None and gap >= -3:
        add(0.7 + (3 + gap) / 30, f"距 52 週高點 {_num(gap)}%")
    days60 = val("inst_buy_days_60d")
    if days60 is not None and days60 >= 65:
        add(0.4 + (days60 - 65) / 100, f"法人 60 日中有 {days60:.0f}% 的交易日買超")
    volume_ratio = val("volume_ratio_5_60")
    if volume_ratio is not None and volume_ratio >= 2:
        add(0.4 + min(volume_ratio, 5) / 20, f"5 日均量為 60 日均量的 {_num(volume_ratio)} 倍")
    cmf = val("cmf_20")
    if cmf is not None and cmf >= 0.2:
        add(0.35 + min(cmf, 0.6) / 4, f"20 日 CMF 資金流 {_num(cmf, 2)}")
    elif cmf is not None and cmf <= -0.2:
        add(0.35 + min(-cmf, 0.6) / 4, f"20 日 CMF 資金流 -{_num(cmf, 2)}")
    if val("trend_aligned") is True:
        add(0.45, "均線多頭排列")
    span = val("holders_weeks_span")
    span_text = f"{max(1, round(span / 7))} 週" if span else None
    big = val("big_holder_pct_change")
    if big is not None and span_text and abs(big) >= 0.5:
        verb = "增" if big > 0 else "減"
        add(
            0.5 + min(abs(big), 5) / 10,
            f"400 張以上大戶持股 {span_text}{verb} {_num(big)} 個百分點",
        )
    retail = val("retail_holders_change_pct")
    if retail is not None and span_text and abs(retail) >= 3:
        verb = "增" if retail > 0 else "減"
        add(0.4 + min(abs(retail), 15) / 30, f"散戶人數 {span_text}{verb} {_num(retail)}%")
    held = val("foreign_holding_pct")
    if held is not None and held >= 30:
        add(0.3 + min(held, 80) / 400, f"外資持股 {_num(held)}%")
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [text for _, _, text in candidates[:limit]]
