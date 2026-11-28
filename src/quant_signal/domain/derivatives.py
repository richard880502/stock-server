"""Domain rows and read models for the official TAIFEX derivatives datasets.

Units are explicit in the field names: ``*_contracts`` are contracts (口), ``*_ntd_k``
are thousands of NT$ (千元, as TAIFEX publishes), ``*_pct`` are percent (82.07 means
82.07 %). Missing values are ``None``, never 0.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

FuturesContract = Literal["TX", "MTX", "TMF"]
Investor = Literal["foreign", "trust", "dealer"]
CallPut = Literal["CALL", "PUT"]
TraderType = Literal["all", "specific"]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True)
    source: str
    available_at: datetime

    @model_validator(mode="after")
    def _check_aware(self) -> _Row:
        _aware(self.available_at)
        return self


class FuturesInstitutionalDay(_Row):
    """三大法人－區分各期貨契約: one contract and investor class for one trading date.

    ``long``/``short`` are the buy/sell sides. ``*_trade_*`` is the day's traded volume,
    ``*_oi_*`` the open interest (未平倉) at the close.
    """

    trading_date: date
    contract: FuturesContract
    investor: Investor
    long_trade_contracts: int
    long_trade_value_ntd_k: int
    short_trade_contracts: int
    short_trade_value_ntd_k: int
    long_oi_contracts: int
    long_oi_value_ntd_k: int
    short_oi_contracts: int
    short_oi_value_ntd_k: int

    @property
    def net_oi_contracts(self) -> int:
        return self.long_oi_contracts - self.short_oi_contracts


class OptionsPcRatioDay(_Row):
    """臺指選擇權 Put/Call 比 for one trading date (ratios are percent as published)."""

    trading_date: date
    put_volume_contracts: int
    call_volume_contracts: int
    volume_ratio_pct: float
    put_oi_contracts: int
    call_oi_contracts: int
    oi_ratio_pct: float


class OptionsInstitutionalDay(_Row):
    """三大法人－選擇權買賣權分計 (TXO): buy/sell sides per call or put and investor."""

    trading_date: date
    contract: Literal["TXO"]
    call_put: CallPut
    investor: Investor
    buy_trade_contracts: int
    buy_trade_value_ntd_k: int
    sell_trade_contracts: int
    sell_trade_value_ntd_k: int
    buy_oi_contracts: int
    buy_oi_value_ntd_k: int
    sell_oi_contracts: int
    sell_oi_value_ntd_k: int

    @property
    def net_oi_contracts(self) -> int:
        return self.buy_oi_contracts - self.sell_oi_contracts


class FuturesLargeTradersDay(_Row):
    """期貨大額交易人未沖銷部位結構 (all delivery months combined) for TX.

    The exchange states TX in TX-equivalent contracts (TX + MTX/4 + TMF/20).
    ``trader_type`` ``all`` is every large trader, ``specific`` the 特定法人 subset.
    """

    trading_date: date
    contract: Literal["TX"]
    trader_type: TraderType
    top5_long_contracts: int
    top5_short_contracts: int
    top10_long_contracts: int
    top10_short_contracts: int
    market_oi_contracts: int


class StockFuturesDay(_Row):
    """One underlying stock/ETF: all its stock-futures products on one trading date.

    Open interest and volume are in *standard-contract equivalents*: the product's
    shares per contract divided by the underlying's standard size (2,000 shares for
    stocks, 10,000 units for ETFs), so a small (100-share) contract counts as 1/20.
    ``*_shares`` hold the exact unit-free totals (shares / ETF units).
    """

    symbol: str
    trading_date: date
    underlying_code: str
    # None when a regular-session row of the underlying had no open interest yet ("-"):
    # missing is not zero (it would read as a -100% one-day change).
    total_open_interest: float | None
    total_volume: float
    open_interest_shares: int | None
    volume_shares: int
    near_month: str | None
    near_settlement: float | None
    near_volume: float | None
    near_open_interest: float | None
    product_count: int


class OptionsOiLevelDay(_Row):
    """The strikes with the largest TXO open interest for one expiry on one date."""

    trading_date: date
    expiry: str
    kind: Literal["monthly", "weekly"]
    expiry_date: date
    max_call_strike: float | None
    max_call_oi_contracts: int | None
    max_put_strike: float | None
    max_put_oi_contracts: int | None
    call_total_oi_contracts: int
    put_total_oi_contracts: int


class VixDay(_Row):
    """臺指選擇權波動率指數 for one date: at the 13:45 close and the last-minute average."""

    trading_date: date
    vix_close: float
    vix_last_minute_avg: float | None


# --- read models -----------------------------------------------------------------------


class DerivativesRows(BaseModel):
    """Raw visible rows for the last sessions (what the pure builders consume)."""

    futures: list[FuturesInstitutionalDay] = []
    pc_ratio: list[OptionsPcRatioDay] = []
    options: list[OptionsInstitutionalDay] = []
    large_traders: list[FuturesLargeTradersDay] = []
    vix: list[VixDay] = []
    oi_levels: list[OptionsOiLevelDay] = []


class MarketDerivativesDay(BaseModel):
    """Everything the market-level derivatives view shows for one date (None = missing)."""

    date: date
    foreign_tx_net_oi: int | None = None
    foreign_tx_equiv_net_oi: float | None = None
    foreign_net_oi_change: float | None = None
    trust_tx_net_oi: int | None = None
    dealer_tx_net_oi: int | None = None
    pc_volume_ratio: float | None = None
    pc_oi_ratio: float | None = None
    foreign_call_net_oi: int | None = None
    foreign_put_net_oi: int | None = None
    top5_net_oi: int | None = None
    top10_net_oi: int | None = None
    vix: float | None = None


class MarketDerivativesLatest(MarketDerivativesDay):
    max_call_oi_strike: float | None = None
    max_put_oi_strike: float | None = None
    max_call_oi_expiry: str | None = None
    max_put_oi_expiry: str | None = None
    # open interest (contracts) at those strikes, straight from options_oi_levels_daily
    max_call_oi_contracts: int | None = None
    max_put_oi_contracts: int | None = None
    weekly_max_call_oi_strike: float | None = None
    weekly_max_put_oi_strike: float | None = None
    weekly_max_call_oi_contracts: int | None = None
    weekly_max_put_oi_contracts: int | None = None
    weekly_expiry: str | None = None


class MarketDerivatives(BaseModel):
    as_of: date
    data_through: date | None
    days: list[MarketDerivativesDay]
    latest: MarketDerivativesLatest | None
    notes: list[str]


class StockFuturesPoint(BaseModel):
    date: date
    open_interest: float | None
    volume: float
    near_month: str | None = None
    near_settlement: float | None = None
    basis_pct: float | None = None


class StockFutures(BaseModel):
    symbol: str
    has_futures: bool
    days: list[StockFuturesPoint]
    oi_change_1d: float | None = None
    oi_change_5d: float | None = None
    notes: list[str]


class StockFuturesMetrics(BaseModel):
    """Latest stock-futures numbers for one underlying (screener / AI context input)."""

    symbol: str
    data_date: date
    open_interest: float | None
    volume: float
    oi_change_1d: float | None
    oi_change_5d: float | None
    near_month: str | None
    basis_pct: float | None
