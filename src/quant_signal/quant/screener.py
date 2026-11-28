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
    from quant_signal.domain.derivatives import StockFuturesMetrics
    from quant_signal.domain.fundamentals import EarningsMetrics, RevenueTrend, ValuationMetrics
    from quant_signal.domain.models import ForeignHolding, ShareholdingPair
    from quant_signal.domain.short_data import (
        DayTradeMetrics,
        InsiderPledge,
        NextEvent,
        ShortLendingMetrics,
    )

VERSION = "screener_v2"
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
        ("revenue_yoy_pct", 0.20, "id"),
        ("revenue_yoy_3m_pct", 0.20, "id"),
        ("eps_yoy_pct", 0.25, "id"),
        ("gross_margin_change_pct", 0.15, "id"),
        ("roe_ttm_pct", 0.20, "id"),
    ],
    "value": [
        ("pe_percentile_250d", 0.35, "neg"),
        ("pb_percentile_250d", 0.25, "neg"),
        ("dividend_yield_pct", 0.25, "id"),
        ("pe_ratio", 0.15, "neg"),
    ],
    "stability": [
        ("volatility_20d", 0.50, "neg"),
        ("max_drawdown_60d", 0.50, "id"),
    ],
}
COMPOSITE_WEIGHTS: dict[str, float] = {
    "momentum": 0.20,
    "trend": 0.10,
    "flow": 0.25,
    "fundamental": 0.20,
    "value": 0.10,
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
        "還原收盤價相對近 250 個交易日最高還原收盤價的差距（不大於 0）",
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
    _m(
        "traded_days_20d",
        "近 20 日有成交天數",
        "liquidity",
        "日",
        0,
        "最近 20 個市場交易日中，該股實際有成交（成交量大於 0）的天數",
        None,
    ),
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
        "revenue_yoy_3m_pct",
        "近 3 月營收年增率",
        "fundamental",
        "%",
        1,
        "最近 3 個月營收合計相對去年同期 3 個月的年增率（缺任一月則無值）",
    ),
    _m(
        "revenue_yoy_12m_pct",
        "近 12 月營收年增率",
        "fundamental",
        "%",
        1,
        "最近 12 個月營收合計相對前 12 個月的年增率（缺任一月則無值）",
    ),
    _m(
        "revenue_growth_streak_months",
        "營收連續年增月數",
        "fundamental",
        "月",
        0,
        "月營收年增率連續大於 0 的月數（截至最新一期）",
    ),
    _m(
        "revenue_is_12m_high",
        "營收創 12 月新高",
        "fundamental",
        "",
        0,
        "最新月營收為近 12 個月最高（需連續 12 個月資料）",
        True,
        "bool",
    ),
    _m("eps_ttm", "近四季 EPS", "fundamental", "元", 2, "最近四季每股盈餘合計（累計財報推算）"),
    _m(
        "eps_yoy_pct",
        "單季 EPS 年增率",
        "fundamental",
        "%",
        1,
        "最新單季 EPS 相對去年同季的年增率（去年同季 EPS 為正才有值）",
    ),
    _m(
        "gross_margin_pct",
        "毛利率",
        "fundamental",
        "%",
        1,
        "最新單季毛利除以營收（金融業無此數字）",
    ),
    _m(
        "gross_margin_change_pct",
        "毛利率年變化",
        "fundamental",
        "個百分點",
        1,
        "最新單季毛利率相對去年同季的變化",
    ),
    _m(
        "operating_margin_pct",
        "營業利益率",
        "fundamental",
        "%",
        1,
        "最新單季營業利益除以營收（金融業無此數字）",
    ),
    _m(
        "roe_ttm_pct",
        "近四季 ROE",
        "fundamental",
        "%",
        1,
        "近四季歸屬母公司淨利除以最新期末母公司權益（非平均權益）",
    ),
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
    _m(
        "pledge_ratio_pct",
        "董監質押比例",
        "holders",
        "%",
        1,
        "董事、監察人持股中設質股數的比例（月資料）",
        False,
    ),
    _m("pe_ratio", "本益比", "valuation", "倍", 1, "官方本益比（虧損公司無值）", None),
    _m("pb_ratio", "股價淨值比", "valuation", "倍", 2, "官方股價淨值比", None),
    _m("dividend_yield_pct", "殖利率", "valuation", "%", 2, "官方現金殖利率"),
    _m(
        "pe_percentile_250d",
        "本益比一年分位",
        "valuation",
        "%",
        0,
        "最新本益比在自身近 250 個交易日中的分位（觀察值少於 60 筆則無值；越高代表相對自身越貴）",
        False,
    ),
    _m(
        "pb_percentile_250d",
        "淨值比一年分位",
        "valuation",
        "%",
        0,
        "最新股價淨值比在自身近 250 個交易日中的分位（觀察值少於 60 筆則無值）",
        False,
    ),
    _m(
        "yield_percentile_250d",
        "殖利率一年分位",
        "valuation",
        "%",
        0,
        "最新殖利率在自身近 250 個交易日中的分位（越高代表殖利率相對自身越高）",
    ),
    _m(
        "sbl_pct_of_shares",
        "借券餘額佔股本",
        "short",
        "%",
        2,
        "借券賣出餘額除以流通在外股數",
        None,
    ),
    _m(
        "sbl_change_5d_pct",
        "借券餘額 5 日變化",
        "short",
        "%",
        1,
        "借券賣出餘額相對 5 個交易日前的變化",
        None,
    ),
    _m(
        "sbl_change_20d_pct",
        "借券餘額 20 日變化",
        "short",
        "%",
        1,
        "借券賣出餘額相對 20 個交易日前的變化",
        None,
    ),
    _m(
        "days_to_cover",
        "回補天數",
        "short",
        "日",
        1,
        "借券賣出餘額除以近期日均成交量",
        None,
    ),
    _m(
        "day_trade_ratio_pct",
        "當沖比",
        "short",
        "%",
        1,
        "當日現股當沖成交股數佔總成交股數",
        None,
    ),
    _m(
        "day_trade_ratio_20d_pct",
        "20 日當沖比",
        "short",
        "%",
        1,
        "近 20 個交易日當沖成交股數佔總成交股數",
        None,
    ),
    _m(
        "has_futures",
        "有個股期貨",
        "futures",
        "",
        0,
        "該股票在期交所有個股期貨商品",
        None,
        "bool",
    ),
    _m(
        "futures_oi_change_5d_pct",
        "期貨未平倉 5 日變化",
        "futures",
        "%",
        1,
        "個股期貨全部契約未平倉量相對 5 個交易日前的變化",
        None,
    ),
    _m(
        "futures_basis_pct",
        "期貨基差",
        "futures",
        "%",
        2,
        "近月個股期貨結算價相對現貨收盤價的差距（正為溢價）",
        None,
    ),
    _m("next_ex_date", "下次除權息日", "events", "", 0, "未來 60 日內最近的除權息日", None, "text"),
    _m("days_to_ex", "距除權息日", "events", "日", 0, "從資料日起算的日曆天數", None),
    _m(
        "next_cash_dividend",
        "預計現金股利",
        "events",
        "元",
        2,
        "下次除權息事件公告的每股現金股利（未公告則無值）",
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
    ("valuation", "估值"),
    ("credit", "信用交易"),
    ("short", "放空與當沖"),
    ("fundamental", "營收與獲利"),
    ("futures", "個股期貨"),
    ("events", "事件"),
    ("risk", "風險"),
    ("liquidity", "流動性"),
)

FACTOR_LABELS: dict[str, tuple[str, str]] = {
    "composite": (
        "綜合評分",
        "動能 20%、趨勢 10%、資金流向 25%、成長與獲利 20%、估值 10%、穩定度 15% 的加權平均"
        "（缺項時依可得項目重新配權；緩步流入不計入）",
    ),
    "momentum": ("價格動能", "20/60/120 日報酬與距 52 週高點的全市場百分位"),
    "trend": ("趨勢", "股價相對 20/60/120 日均線乖離的全市場百分位"),
    "flow": ("資金流向", "法人買超佔成交量與股本、買超日占比、CMF 與融資變化的全市場百分位"),
    "accumulation": (
        "緩步流入",
        "近 60 個交易日法人多數日子淨買超、400 張以上大戶持股比例上升、近 20 日股價漲跌幅不大的程度"
        "（百分位；集保資料缺時重新配權）",
    ),
    "fundamental": (
        "成長與獲利",
        "月營收年增、近 3 月營收年增、單季 EPS 年增、毛利率年變化與近四季 ROE 的全市場百分位"
        "（缺項時重新配權，可得權重不足一半則無分數）",
    ),
    "value": (
        "估值",
        "本益比與股價淨值比相對自身一年的分位（越低越高分）、殖利率與本益比的全市場百分位"
        "（虧損公司本益比無值；缺項時重新配權）",
    ),
    "stability": ("穩定度", "低波動與較小回檔的全市場百分位"),
}

# Compact column headers (at most 8 full-width characters) for narrow tables; ``label`` stays
# the full name. Every metric key and every score key (plus ``composite``) has one.
SHORT_LABELS: dict[str, str] = {
    "composite": "綜合評分",
    "momentum": "價格動能",
    "trend": "趨勢",
    "flow": "資金流向",
    "accumulation": "緩步流入",
    "fundamental": "成長與獲利",
    "value": "估值",
    "stability": "穩定度",
    "change_pct": "單日漲跌",
    "ret_5d": "5日報酬",
    "ret_20d": "20日報酬",
    "ret_60d": "60日報酬",
    "ret_120d": "120日報酬",
    "ma20_gap_pct": "距20日均線",
    "ma60_gap_pct": "距60日均線",
    "ma120_gap_pct": "距120日均線",
    "high_52w_gap_pct": "距52週高點",
    "trend_aligned": "均線多頭",
    "avg_turnover_20d": "20日均成交值",
    "volume_ratio_5_60": "5/60量比",
    "market_cap": "市值",
    "turnover_rate_20d": "20日週轉率",
    "sessions": "資料天數",
    "traded_days_20d": "20日成交日",
    "volatility_20d": "20日波動率",
    "max_drawdown_60d": "60日最大回檔",
    "foreign_net_5d": "外資5日買賣",
    "foreign_net_20d": "外資20日買賣",
    "foreign_net_60d": "外資60日買賣",
    "trust_net_5d": "投信5日買賣",
    "trust_net_20d": "投信20日買賣",
    "trust_net_60d": "投信60日買賣",
    "dealer_net_20d": "自營20日買賣",
    "total_net_5d": "法人5日買賣",
    "total_net_20d": "法人20日買賣",
    "total_net_60d": "法人60日買賣",
    "inst_flow_ratio_20d": "法人20日佔量",
    "inst_flow_ratio_60d": "法人60日佔量",
    "foreign_flow_ratio_20d": "外資20日佔量",
    "trust_flow_ratio_20d": "投信20日佔量",
    "foreign_streak": "外資連買賣",
    "trust_streak": "投信連買賣",
    "inst_buy_days_20d": "20日買超天數比",
    "inst_buy_days_60d": "60日買超天數比",
    "inst_flow_to_shares_20d": "法人20日佔股本",
    "inst_flow_to_shares_60d": "法人60日佔股本",
    "cmf_20": "20日CMF",
    "up_volume_ratio_20d": "20日上漲量比",
    "margin_change_20d_pct": "融資20日變化",
    "short_change_20d_pct": "融券20日變化",
    "chip_transfer_20d": "法人買融資退",
    "revenue_period": "營收月份",
    "revenue_yoy_pct": "營收年增",
    "revenue_mom_pct": "營收月增",
    "revenue_cum_yoy_pct": "累計營收年增",
    "big_holder_pct": "400張大戶持股",
    "whale_pct": "千張大戶持股",
    "big_holder_pct_change": "大戶持股變化",
    "whale_pct_change": "千張大戶變化",
    "retail_holders_change_pct": "散戶人數變化",
    "holders_weeks_span": "比較間隔",
    "holders_data_date": "集保資料日",
    "foreign_holding_pct": "外資持股",
    "pledge_ratio_pct": "董監質押",
    "pe_ratio": "本益比",
    "pb_ratio": "股價淨值比",
    "dividend_yield_pct": "殖利率",
    "pe_percentile_250d": "本益比分位",
    "pb_percentile_250d": "淨值比分位",
    "yield_percentile_250d": "殖利率分位",
    "revenue_yoy_3m_pct": "近3月營收年增",
    "revenue_yoy_12m_pct": "近12月營收年增",
    "revenue_growth_streak_months": "營收連增月數",
    "revenue_is_12m_high": "營收12月新高",
    "eps_ttm": "近四季EPS",
    "eps_yoy_pct": "單季EPS年增",
    "gross_margin_pct": "毛利率",
    "gross_margin_change_pct": "毛利率年變化",
    "operating_margin_pct": "營業利益率",
    "roe_ttm_pct": "近四季ROE",
    "sbl_pct_of_shares": "借券佔股本",
    "sbl_change_5d_pct": "借券5日變化",
    "sbl_change_20d_pct": "借券20日變化",
    "days_to_cover": "回補天數",
    "day_trade_ratio_pct": "當沖比",
    "day_trade_ratio_20d_pct": "20日當沖比",
    "has_futures": "有個股期貨",
    "futures_oi_change_5d_pct": "期貨OI5日變化",
    "futures_basis_pct": "期貨基差",
    "next_ex_date": "下次除息日",
    "days_to_ex": "距除息日",
    "next_cash_dividend": "預計現金股利",
}

# One-line notes shown with a metric group in ``/market/screener/meta``.
GROUP_NOTES: dict[str, str] = {
    "short": (
        "借券賣出與當沖資料僅供觀察，不計入任何因子分數：借券餘額高可能是避險或套利、"
        "也可能是看空，當沖比高只代表交易活躍，兩者方向都不明確。"
    ),
    "valuation": "官方本益比、股價淨值比與殖利率，以及各自相對自身近一年的分位（非與同業比較）。",
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


def align_rows(
    rows: list[tuple], sessions: list[date], reported: set[date] | frozenset[date]
) -> list[tuple]:
    """Align one stock's traded rows to its market's session calendar.

    The data sources drop no-trade rows, so the rows alone would make "20 sessions" mean
    "20 traded days". From the stock's first row onward every market session gets a row:
    a missing one is a no-trade day (volume and turnover 0, price carried forward, zero
    change, credit balances carried with a zero change, institutional nets 0 when the
    market's report exists for that session and unknown otherwise). Nothing is padded
    before the first row, so new listings stay short.
    """
    if not rows:
        return []
    by_date = {row[0]: row for row in rows}
    first = rows[0][0]
    out: list[tuple] = []
    previous: tuple | None = None
    for day in sorted(set(sessions) | set(by_date)):
        if day < first:
            continue
        row = by_date.get(day)
        if row is None:
            assert previous is not None  # the first session is the first row itself
            price = previous[1]
            net = 0 if day in reported else None
            row = (
                day,
                price,
                0.0,
                price,
                price,
                0,
                0.0,
                net,
                net,
                net,
                net,
                previous[11],
                None if previous[11] is None else 0,
                previous[13],
                None if previous[13] is None else 0,
            )
        out.append(row)
        previous = row
    return out


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
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    change: np.ndarray | None = None,
) -> float | None:
    """Sum of money-flow volume over sum of volume (inputs are the window itself).

    A bar with no range (high == low, e.g. locked limit up/down) uses the sign of its
    price ``change`` as the multiplier (+1 up, -1 down, 0 flat) instead of 0.
    """
    total = float(np.sum(volume))
    if total <= 0:
        return None
    span = high - low
    locked = np.sign(np.nan_to_num(change)) if change is not None else np.zeros_like(span)
    with np.errstate(divide="ignore", invalid="ignore"):
        multiplier = np.where(span > 0, ((close - low) - (high - close)) / span, locked)
    return float(np.sum(multiplier * volume)) / total


# --------------------------------------------------------------------- flow maths


def _flow_window(values: np.ndarray, end: int, size: int) -> np.ndarray | None:
    """Slice of ``size`` sessions ending at index ``end`` (inclusive).

    When the newest report is not out yet (``end`` is before the last session) a stock
    that does have ``size`` sessions gets the shorter window ending at ``end``; callers
    still require half of ``size`` reported sessions inside it.
    """
    start = end + 1 - size
    if start >= 0:
        return values[start : end + 1]
    if len(values) >= size and end >= 0:
        return values[: end + 1]
    return None


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


EX_KINDS = frozenset({"ex_dividend", "ex_rights", "ex_rights_dividend"})
OFFICIAL_KEYS = (
    "pledge_ratio_pct pe_ratio pb_ratio dividend_yield_pct pe_percentile_250d "
    "pb_percentile_250d yield_percentile_250d revenue_yoy_3m_pct revenue_yoy_12m_pct "
    "revenue_growth_streak_months revenue_is_12m_high eps_ttm eps_yoy_pct gross_margin_pct "
    "gross_margin_change_pct operating_margin_pct roe_ttm_pct sbl_pct_of_shares "
    "sbl_change_5d_pct sbl_change_20d_pct days_to_cover day_trade_ratio_pct "
    "day_trade_ratio_20d_pct has_futures futures_oi_change_5d_pct futures_basis_pct "
    "next_ex_date days_to_ex next_cash_dividend"
).split()


def official_metrics(
    trading_date: date,
    *,
    valuation: ValuationMetrics | None = None,
    trend: RevenueTrend | None = None,
    earnings: EarningsMetrics | None = None,
    short_lending: ShortLendingMetrics | None = None,
    day_trade: DayTradeMetrics | None = None,
    pledge: InsiderPledge | None = None,
    futures: StockFuturesMetrics | None = None,
    has_futures: bool | None = None,
    event: NextEvent | None = None,
) -> dict[str, Any]:
    """Metrics from the official valuation / revenue / earnings / short / futures / event feeds.

    Every key is present; a missing input leaves its metrics ``None`` (never 0).
    ``has_futures`` is ``None`` when the futures feed holds no data at all.
    """
    out: dict[str, Any] = dict.fromkeys(OFFICIAL_KEYS)
    if valuation is not None:
        for key in (
            "pe_ratio",
            "pb_ratio",
            "dividend_yield_pct",
            "pe_percentile_250d",
            "pb_percentile_250d",
            "yield_percentile_250d",
        ):
            out[key] = getattr(valuation, key)
    if trend is not None:
        out["revenue_yoy_3m_pct"] = trend.yoy_3m_pct
        out["revenue_yoy_12m_pct"] = trend.yoy_12m_pct
        out["revenue_growth_streak_months"] = trend.growth_streak_months
        out["revenue_is_12m_high"] = trend.is_12m_high
    if earnings is not None:
        out["eps_ttm"] = earnings.eps_ttm
        out["eps_yoy_pct"] = earnings.eps_yoy_pct
        out["gross_margin_pct"] = earnings.gross_margin_pct
        out["gross_margin_change_pct"] = earnings.gross_margin_yoy_pp
        out["operating_margin_pct"] = earnings.operating_margin_pct
        out["roe_ttm_pct"] = earnings.roe_ttm_pct
    if short_lending is not None:
        out["sbl_pct_of_shares"] = short_lending.sbl_pct_of_shares
        out["sbl_change_5d_pct"] = short_lending.sbl_change_5d_pct
        out["sbl_change_20d_pct"] = short_lending.sbl_change_20d_pct
        out["days_to_cover"] = short_lending.days_to_cover
    if day_trade is not None:
        out["day_trade_ratio_pct"] = day_trade.day_trade_ratio_pct
        out["day_trade_ratio_20d_pct"] = day_trade.day_trade_ratio_20d_pct
    if pledge is not None:
        out["pledge_ratio_pct"] = pledge.pledge_ratio_pct
    out["has_futures"] = has_futures
    if futures is not None:
        out["futures_oi_change_5d_pct"] = futures.oi_change_5d
        out["futures_basis_pct"] = futures.basis_pct
    if event is not None and event.kind in EX_KINDS:
        out["next_ex_date"] = event.date.isoformat()
        out["days_to_ex"] = (event.date - trading_date).days
        out["next_cash_dividend"] = event.cash_dividend
    return out


def compute_metrics(
    history: StockHistory,
    flow_through: date | None,
    holders: ShareholdingPair | None = None,
    foreign: ForeignHolding | None = None,
    **official: Any,
) -> dict[str, Any] | None:
    """All spec metrics for the newest session in ``history``; ``None`` if under 20 sessions.

    ``flow_through`` is the newest session (≤ the newest date) with institutional data.
    ``holders`` / ``foreign`` are the point-in-time TDCC pair and foreign-holding row;
    ``official`` forwards the optional per-stock feed inputs of :func:`official_metrics`.
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
    m["traded_days_20d"] = int(np.sum(volume[-20:] > 0))
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
        history.high[tail], history.low[tail], close[tail], volume[tail], history.change[tail]
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
    m.update(official_metrics(history.dates[-1], **official))
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


# Thresholds above which an official-data metric earns a highlight (the single place to tune).
HIGHLIGHT_THRESHOLDS: dict[str, float] = {
    "pe_percentile_max": 15,  # P/E at or below this percentile of its own year
    "dividend_yield_min": 5,  # percent
    "eps_yoy_min": 30,  # single-quarter EPS growth, percent
    "gross_margin_change_min": 2,  # percentage points vs the same quarter last year
    "revenue_streak_min": 6,  # consecutive months of positive revenue YoY
    "sbl_pct_min": 2,  # SBL balance as percent of shares outstanding
    "day_trade_min": 50,  # day-trade share of volume, percent
    "ex_days_max": 7,  # calendar days to the next ex-dividend date
    "pledge_min": 50,  # insiders' pledge ratio, percent (also sets the high_pledge flag)
}
PLEDGE_FLAG_MIN = HIGHLIGHT_THRESHOLDS["pledge_min"]

# Highlight priority: each candidate has a strength; the strongest ``limit`` win and ties
# keep the order they were added in. Base strengths (before the small size bonus): 處置股 1.0,
# 法人買融資退 .95, 注意股 / 52 週高點 .7, 董監質押 .65, 近期除息 .6, 營收連增 .55,
# EPS 年增 .5, 毛利率年增 .5, 本益比低分位 .45, 殖利率 .4, 借券佔股本 .4, 當沖比 .35.


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
        add(0.95, "法人買、融資退")
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
        if yoy >= 1000:
            add(0.3 + 0.5, f"營收年增逾 {math.floor(yoy / 100)} 倍")
        else:
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
    th = HIGHLIGHT_THRESHOLDS
    pledge = val("pledge_ratio_pct")
    if pledge is not None and pledge >= th["pledge_min"]:
        add(0.65 + min(pledge, 100) / 1000, f"董監質押 {_num(pledge, 0)}%")
    days_to_ex, cash = val("days_to_ex"), val("next_cash_dividend")
    if days_to_ex is not None and 0 <= days_to_ex <= th["ex_days_max"]:
        when = "今日除息" if days_to_ex == 0 else f"{days_to_ex} 日後除息"
        add(0.6 + (th["ex_days_max"] - days_to_ex) / 100, when + (f" {cash:g} 元" if cash else ""))
    streak_m = val("revenue_growth_streak_months")
    if streak_m is not None and streak_m >= th["revenue_streak_min"]:
        add(0.55 + min(streak_m, 24) / 240, f"營收連 {streak_m} 個月年增")
    eps_yoy = val("eps_yoy_pct")
    if eps_yoy is not None and eps_yoy >= th["eps_yoy_min"]:
        add(0.5 + min(eps_yoy, 200) / 2000, f"單季 EPS 年增 {_num(eps_yoy, 0)}%")
    margin_pp = val("gross_margin_change_pct")
    if margin_pp is not None and margin_pp >= th["gross_margin_change_min"]:
        add(0.5 + min(margin_pp, 10) / 100, f"毛利率年增 {_num(margin_pp)} 個百分點")
    pe_pct = val("pe_percentile_250d")
    if pe_pct is not None and pe_pct <= th["pe_percentile_max"]:
        add(0.45 + (th["pe_percentile_max"] - pe_pct) / 300, f"本益比位於近一年 {pe_pct:.0f}% 分位")
    dividend = val("dividend_yield_pct")
    if dividend is not None and dividend >= th["dividend_yield_min"]:
        add(0.4 + min(dividend, 10) / 100, f"殖利率 {_num(dividend)}%")
    sbl = val("sbl_pct_of_shares")
    if sbl is not None and sbl >= th["sbl_pct_min"]:
        add(0.4 + min(sbl, 10) / 100, f"借券餘額佔股本 {_num(sbl)}%")
    day_trade = val("day_trade_ratio_pct")
    if day_trade is not None and day_trade >= th["day_trade_min"]:
        add(0.35 + min(day_trade, 100) / 1000, f"當沖比 {_num(day_trade, 0)}%")
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [text for _, _, text in candidates[:limit]]
