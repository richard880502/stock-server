"""Domain rows for securities-lending short selling, day trading, insider pledges and
dividend events (TWSE / TPEx official data).

Share counts are in SHARES (not lots); money in NT$ unless a field says otherwise.
Missing values are ``None``, never 0. ``revision`` starts at 1 and grows only when a later
fetch of the same business key returned different numbers (the exchanges re-publish
day-trade figures on T+1 and T+2); reads take the newest revision visible at the cutoff.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ShortLendingDay(BaseModel):
    """One stock's 借券賣出 (securities-lending short sale) balance for one session."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    trading_date: date
    source: str
    sbl_prev_balance: int | None = None  # shares
    sbl_sold: int | None = None  # shares sold short through lending today
    sbl_returned: int | None = None  # shares returned today
    sbl_adjustment: int | None = None  # shares, 當日調整
    sbl_balance: int | None = None  # shares, end of day outstanding
    sbl_limit_next_day: int | None = None  # shares, 次一營業日可借券賣出限額
    available_at: datetime
    revision: int = Field(default=1, ge=1)


class DayTradeDay(BaseModel):
    """One stock's 現股當沖 totals for one session (all day traders combined)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    trading_date: date
    source: str
    day_trade_volume: int | None = None  # shares (one side)
    day_trade_buy_amount: int | None = None  # NT$
    day_trade_sell_amount: int | None = None  # NT$
    available_at: datetime
    revision: int = Field(default=1, ge=1)


class InsiderHolding(BaseModel):
    """Directors / supervisors / managers / large holders of one company for one month."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    period: str  # "YYYY-MM" of 資料年月
    source: str
    insider_count: int
    holding_shares: int
    pledged_shares: int
    pledge_ratio_pct: float | None  # pledged / holding * 100, None when holding is 0
    published_on: date | None = None  # 出表日期
    available_at: datetime
    revision: int = Field(default=1, ge=1)


DividendKind = Literal["ex_dividend", "ex_rights", "ex_rights_dividend"]


class DividendEvent(BaseModel):
    """One scheduled 除權/除息 date with the structured amounts the exchange forecasts."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    ex_date: date
    kind: DividendKind
    source: str
    cash_dividend_per_share: float | None = None  # NT$ per share; None until announced
    stock_dividend_per_share: float | None = None  # new shares per 1 share held (0.05 = 50/1000)
    cash_capital_increase_ratio: float | None = None  # 現金增資配股率, new shares per share
    subscription_price: float | None = None  # NT$ per new share, None until announced
    available_at: datetime
    revision: int = Field(default=1, ge=1)


class ShortLendingMetrics(BaseModel):
    """Derived short-lending numbers for one symbol as of a session."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    trading_date: date
    sbl_balance_shares: int | None = None
    sbl_change_1d_pct: float | None = None
    sbl_change_5d_pct: float | None = None
    sbl_change_20d_pct: float | None = None
    sbl_pct_of_shares: float | None = None
    sbl_sold_pct_of_volume: float | None = None
    days_to_cover: float | None = None


class DayTradeMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    trading_date: date
    day_trade_ratio_pct: float | None = None
    day_trade_ratio_5d_pct: float | None = None
    day_trade_ratio_20d_pct: float | None = None
    day_trade_net_pnl_proxy: int | None = None  # sell amount - buy amount, NT$


class NextEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    date: date
    kind: Literal["ex_dividend", "ex_rights", "ex_rights_dividend", "investor_conference"]
    cash_dividend: float | None = None
    stock_dividend: float | None = None


class InsiderPledge(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    period: str
    pledge_ratio_pct: float | None
    holding_shares: int
    pledged_shares: int


class ShortDay(BaseModel):
    date: date
    sbl_balance_lots: float | None = None
    sbl_sold_lots: float | None = None
    sbl_returned_lots: float | None = None
    margin_short_balance_lots: int | None = None
    day_trade_ratio_pct: float | None = None


class UpcomingEvent(BaseModel):
    date: date
    kind: Literal["ex_dividend", "ex_rights", "ex_rights_dividend", "investor_conference"]
    label: str
    cash_dividend: float | None = None
    stock_dividend: float | None = None


class StockShortInterest(BaseModel):
    """Response of ``GET /api/v1/stocks/{symbol}/short-interest``."""

    symbol: str
    as_of: date
    days: list[ShortDay]
    sbl_change_5d_pct: float | None = None
    sbl_change_20d_pct: float | None = None
    sbl_pct_of_shares: float | None = None
    days_to_cover: float | None = None
    day_trade_ratio_5d_pct: float | None = None
    day_trade_ratio_20d_pct: float | None = None
    insiders: InsiderPledge | None = None
    upcoming_events: list[UpcomingEvent]
    notes: list[str]
