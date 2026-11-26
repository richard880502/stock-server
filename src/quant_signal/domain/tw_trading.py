"""Taiwan equity trading rules shared by backtests and (later) paper trading.

Pure functions only, no I/O. All money/price arithmetic uses ``Decimal`` so tick and
fee rounding never suffers from binary floating point error.

Rates checked against the TWSE trading-system page (twse.com.tw/en/products/system/
trading.html): brokerage fee 0.1425% (max, brokers may discount), stock tax 0.3%,
ETF tax 0.1%, stock tick bands. NOT verified from an official page: the ETF tick
bands, and that the 0.15% day-trade rate is still extended (the TWSE page states the
reduced rate ran to 2024-12-31; it has been extended by law, confirm before relying
on it). All rates are therefore parameters of ``TWCostConfig`` and never hard-coded in
callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Literal

BOARD_LOT = 1_000
PRICE_LIMIT_PCT = Decimal("0.10")

D = Decimal


@dataclass(frozen=True)
class TWCostConfig:
    """Configurable rates. Defaults follow the statutory / standard schedule."""

    commission_rate: Decimal = D("0.001425")
    commission_discount: Decimal = D("1.0")  # 0.6 means "6 折"
    min_commission: int = 20  # NT$ per fill
    stock_tax_rate: Decimal = D("0.003")
    etf_tax_rate: Decimal = D("0.001")
    day_trade_tax_rate: Decimal = D("0.0015")  # 現股當沖, stocks only


DEFAULT_COSTS = TWCostConfig()


def is_etf(symbol: str) -> bool:
    """Heuristic: Taiwan ETF codes start with "00" (0050, 00878, 006208, 00631L).

    Accepts "0050", "0050.TW", "00878.TWO". Plain stocks start with 1-9. Not exhaustive
    (e.g. some 9xxxx beneficiary certificates), but correct for listed ETFs.
    """
    code = symbol.strip().upper().split(".")[0]
    return code.startswith("00")


def commission(amount: float | Decimal, config: TWCostConfig = DEFAULT_COSTS) -> int:
    """Brokerage fee for one fill, in whole NT$.

    ``floor(amount * rate * discount)`` (brokers commonly truncate), then raised to the
    per-fill minimum. Zero amount means no fill and no fee.
    """
    value = D(str(amount))
    if value <= 0:
        return 0
    fee = int((value * config.commission_rate * config.commission_discount).to_integral_value(
        ROUND_FLOOR
    ))
    return max(fee, config.min_commission)


def transaction_tax(
    amount: float | Decimal,
    *,
    symbol: str = "",
    etf: bool | None = None,
    day_trade: bool = False,
    config: TWCostConfig = DEFAULT_COSTS,
) -> int:
    """Securities transaction tax on a SELL, in whole NT$ (floored). Buys pay none.

    Day-trade rate applies to stocks only; ETFs keep their own rate.
    """
    value = D(str(amount))
    if value <= 0:
        return 0
    etf_flag = is_etf(symbol) if etf is None else etf
    if etf_flag:
        rate = config.etf_tax_rate
    elif day_trade:
        rate = config.day_trade_tax_rate
    else:
        rate = config.stock_tax_rate
    return int((value * rate).to_integral_value(ROUND_FLOOR))


def fill_cost(
    side: Literal["buy", "sell"],
    amount: float | Decimal,
    *,
    symbol: str = "",
    etf: bool | None = None,
    day_trade: bool = False,
    config: TWCostConfig = DEFAULT_COSTS,
) -> int:
    """Commission plus (for sells) tax for one fill."""
    total = commission(amount, config)
    if side == "sell":
        total += transaction_tax(amount, symbol=symbol, etf=etf, day_trade=day_trade, config=config)
    return total


def tick_size(price: float | Decimal, *, etf: bool = False) -> Decimal:
    """Legal price increment at ``price``. ETFs: 0.01 below 50, else 0.05."""
    p = D(str(price))
    if etf:
        return D("0.01") if p < 50 else D("0.05")
    if p < 10:
        return D("0.01")
    if p < 50:
        return D("0.05")
    if p < 100:
        return D("0.1")
    if p < 500:
        return D("0.5")
    if p < 1000:
        return D("1")
    return D("5")


def _round_to_tick(price: Decimal, mode: str, etf: bool) -> Decimal:
    tick = tick_size(price, etf=etf)
    return (price / tick).to_integral_value(mode) * tick


def round_to_tick_down(price: float | Decimal, *, etf: bool = False) -> Decimal:
    return _round_to_tick(D(str(price)), ROUND_FLOOR, etf)


def round_to_tick_up(price: float | Decimal, *, etf: bool = False) -> Decimal:
    return _round_to_tick(D(str(price)), ROUND_CEILING, etf)


def price_limits(
    reference: float | Decimal, *, etf: bool = False
) -> tuple[Decimal, Decimal]:
    """(limit_down, limit_up): reference ±10% rounded inward to legal ticks."""
    ref = D(str(reference))
    up = round_to_tick_down(ref * (1 + PRICE_LIMIT_PCT), etf=etf)
    down = round_to_tick_up(ref * (1 - PRICE_LIMIT_PCT), etf=etf)
    return down, up


def limit_lock_direction(
    open_: float, high: float, low: float, limit_up: float, limit_down: float
) -> Literal["up", "down"] | None:
    """A session is locked when it never traded away from the limit price.

    Locked up: open, high and low all equal limit_up (nothing could be bought).
    Locked down: all equal limit_down (nothing could be sold).
    """
    if low == limit_up and open_ == limit_up:
        return "up"
    if high == limit_down and open_ == limit_down:
        return "down"
    return None


def is_limit_locked(
    open_: float, high: float, low: float, limit_up: float, limit_down: float
) -> bool:
    return limit_lock_direction(open_, high, low, limit_up, limit_down) is not None


def round_lots(shares: int) -> int:
    """Floor to whole board lots (1,000 shares)."""
    return (max(shares, 0) // BOARD_LOT) * BOARD_LOT
