"""Pure order-matching and fill arithmetic for paper trading.

No I/O. The worker feeds observations (live quotes and minute bars) and applies the
result; every price, fee and tax goes through ``domain/tw_trading``.

Rules (see PAPER_SPEC.md section 3):

* An observation counts for an order only if it is strictly after the order was created
  and inside the order's session date (09:00 to 13:35 Taipei: the closing-auction print
  carries 13:30 but arrives a little later).
* Market buy: first observation's price plus ``slippage_ticks``, capped at limit-up, and
  no fill while the price sits at limit-up. Market sell mirrors it.
* Limit buy at L: first observation with price <= L, filled at
  ``min(L, price + slippage)``. Limit sell at L: price >= L, ``max(L, price - slippage)``.
* A minute bar fills a limit buy when ``low <= L`` (at ``min(L, open + slippage)`` if the
  bar opened at or under L, else at L); market orders fill at ``open +/- slippage``.
* Orders are all-or-nothing and produce at most one fill.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from quant_signal.domain.tw_trading import (
    BOARD_LOT,
    TWCostConfig,
    commission,
    is_etf,
    is_on_tick,
    move_ticks,
    price_limit_pct_for,
    price_limits,
    transaction_tax,
)
from quant_signal.quant.paper_calendar import local, observation_window

D = Decimal
Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]
PriceSource = Literal["tick", "snapshot", "minute_bar", "open"]


@dataclass(frozen=True)
class Observation:
    """One price we saw. For ``minute_bar``, ``price`` is the bar's open and ``time`` its
    start; ``high``/``low`` are set only for bars."""

    time: datetime
    source: PriceSource
    price: Decimal
    high: Decimal | None = None
    low: Decimal | None = None


@dataclass(frozen=True)
class OrderSpec:
    side: Side
    order_type: OrderType
    limit_price: Decimal | None
    created_at: datetime
    session_date: date
    etf: bool = False


@dataclass(frozen=True)
class Match:
    price: Decimal
    time: datetime
    source: PriceSource


@dataclass(frozen=True)
class FillAmounts:
    amount: int
    commission: int
    tax: int
    day_trade: bool


def to_money(value: Decimal | float | int) -> int:
    """Whole NT$, half up."""
    return int(D(str(value)).quantize(D(1), rounding=ROUND_HALF_UP))


def to_price(value: Decimal | float | int | str) -> Decimal:
    return D(str(value)).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def cost_config(commission_discount: Decimal | float) -> TWCostConfig:
    return TWCostConfig(commission_discount=D(str(commission_discount)))


def symbol_is_etf(symbol: str) -> bool:
    return is_etf(symbol)


# --- placement helpers -------------------------------------------------------------


def resolve_reference(
    session_date: date,
    *,
    quote_reference: Decimal | None,
    quote_price: Decimal | None,
    quote_date: date | None,
    official_close: Decimal | None,
    official_date: date | None,
) -> Decimal | None:
    """The price the +/-10% band is measured from for ``session_date``.

    The exchange's own reference (``quote_latest.reference``) wins when the quote is
    from that very session. Otherwise it is the newest close before the session: the
    last trade of an earlier day if it is newer than the official daily row, else the
    official close.
    """
    if quote_date == session_date and quote_reference:
        return quote_reference
    quote_ok = quote_price is not None and quote_date is not None and quote_date < session_date
    official_ok = (
        official_close is not None and official_date is not None and (official_date < session_date)
    )
    if quote_ok and official_ok:
        assert quote_date is not None and official_date is not None
        return quote_price if quote_date > official_date else official_close
    if quote_ok:
        return quote_price
    if official_ok:
        return official_close
    return None


def limits_for(reference: Decimal, etf: bool) -> tuple[Decimal, Decimal]:
    """(limit_down, limit_up) as Decimals, at the ordinary 10% band."""
    down, up = price_limits(reference, etf=etf)
    return D(str(down)), D(str(up))


def limits_for_symbol(
    reference: Decimal, symbol: str
) -> tuple[Decimal, Decimal] | tuple[None, None]:
    """(limit_down, limit_up) for ``symbol``, or (None, None) when it has no usable limit."""
    pct = price_limit_pct_for(symbol)
    if pct is None:
        return None, None
    down, up = price_limits(reference, etf=symbol_is_etf(symbol), pct=pct)
    return D(str(down)), D(str(up))


def past_session_reference(
    *,
    day_close: Decimal | None,
    day_change: Decimal | None,
    prior_close: Decimal | None,
) -> Decimal | None:
    """The reference price of an already finished session, from official daily rows only.

    That day's own row says it directly (``close - change``); failing that, the newest
    official close before it. Used when reconciling a session older than today, where the
    live quote belongs to a later day and says nothing about that day's band.
    """
    if day_close is not None and day_change is not None and day_close - day_change > 0:
        return to_price(day_close - day_change)
    return prior_close


def limit_price_problem(
    price: Decimal, *, etf: bool, limit_down: Decimal | None, limit_up: Decimal | None
) -> str | None:
    """A Traditional-Chinese reason when ``price`` cannot be a legal limit price."""
    if price <= 0:
        return "限價必須大於 0"
    if price != price.quantize(D("0.01")) or not is_on_tick(price, etf=etf):
        return f"限價 {price:f} 不符合升降單位"
    if limit_up is not None and limit_down is not None and not (limit_down <= price <= limit_up):
        return f"限價 {price:f} 超出漲跌幅限制（{limit_down:f} ~ {limit_up:f}）"
    return None


def reserve_for_buy(shares: int, price_cap: Decimal, commission_discount: Decimal | float) -> int:
    """Cash set aside for a buy: shares x cap price plus the brokerage fee on that amount."""
    amount = to_money(D(shares) * price_cap)
    return amount + commission(amount, cost_config(commission_discount))


def estimate_price(
    side: Side,
    order_type: OrderType,
    limit_price: Decimal | None,
    last_price: Decimal | None,
    *,
    etf: bool,
    slippage_ticks: int,
    limit_down: Decimal | None,
    limit_up: Decimal | None,
) -> Decimal | None:
    """Where a fill would land if it happened at ``last_price`` right now (for previews)."""
    if side == "buy":
        slipped = (
            None if last_price is None else move_ticks(last_price, slippage_ticks, "up", etf=etf)
        )
        if order_type == "market":
            if slipped is None:
                return limit_up
            return min(slipped, limit_up) if limit_up is not None else slipped
        assert limit_price is not None
        return limit_price if slipped is None else min(limit_price, slipped)
    slipped = (
        None if last_price is None else move_ticks(last_price, slippage_ticks, "down", etf=etf)
    )
    if order_type == "market":
        if slipped is None:
            return limit_down
        return max(slipped, limit_down) if limit_down is not None else slipped
    assert limit_price is not None
    return limit_price if slipped is None else max(limit_price, slipped)


# --- matching ----------------------------------------------------------------------


def _usable(order: OrderSpec, observation: Observation) -> bool:
    if observation.time <= order.created_at:
        return False
    moment = local(observation.time)
    if moment.date() != order.session_date:
        return False
    first, last = observation_window(order.session_date)
    return first <= moment <= last


def _match_point(
    order: OrderSpec,
    price: Decimal,
    limit_down: Decimal | None,
    limit_up: Decimal | None,
    slippage_ticks: int,
) -> Decimal | None:
    """Fill price for one traded price, or None (no fill / locked at the limit).

    ``None`` limits mean the symbol has no daily price limit: nothing is ever "locked"
    and nothing is capped.
    """
    etf = order.etf
    if order.side == "buy":
        if limit_up is not None and price >= limit_up:
            return None  # locked limit-up: nobody sells
        slipped = move_ticks(price, slippage_ticks, "up", etf=etf)
        if order.order_type == "market":
            return _cap(slipped, limit_up, "up")
        assert order.limit_price is not None
        if price > order.limit_price:
            return None
        return _cap(min(order.limit_price, slipped), limit_up, "up")
    if limit_down is not None and price <= limit_down:
        return None  # locked limit-down: nobody buys
    slipped = move_ticks(price, slippage_ticks, "down", etf=etf)
    if order.order_type == "market":
        return _cap(slipped, limit_down, "down")
    assert order.limit_price is not None
    if price < order.limit_price:
        return None
    return _cap(max(order.limit_price, slipped), limit_down, "down")


def _cap(price: Decimal, bound: Decimal | None, side: Literal["up", "down"]) -> Decimal:
    if bound is None:
        return price
    return min(price, bound) if side == "up" else max(price, bound)


def _match_bar(
    order: OrderSpec,
    bar: Observation,
    limit_down: Decimal | None,
    limit_up: Decimal | None,
    slippage_ticks: int,
) -> Decimal | None:
    assert bar.high is not None and bar.low is not None
    etf = order.etf
    if order.side == "buy":
        if limit_up is not None and bar.low >= limit_up:
            return None
        slipped = move_ticks(bar.price, slippage_ticks, "up", etf=etf)
        if order.order_type == "market":
            return _cap(slipped, limit_up, "up")
        assert order.limit_price is not None
        if bar.low > order.limit_price:
            return None
        if bar.price <= order.limit_price:
            return _cap(min(order.limit_price, slipped), limit_up, "up")
        return order.limit_price
    if limit_down is not None and bar.high <= limit_down:
        return None
    slipped = move_ticks(bar.price, slippage_ticks, "down", etf=etf)
    if order.order_type == "market":
        return _cap(slipped, limit_down, "down")
    assert order.limit_price is not None
    if bar.high < order.limit_price:
        return None
    if bar.price >= order.limit_price:
        return _cap(max(order.limit_price, slipped), limit_down, "down")
    return order.limit_price


def match_order(
    order: OrderSpec,
    observations: Iterable[Observation],
    *,
    limit_down: Decimal | None,
    limit_up: Decimal | None,
    slippage_ticks: int,
) -> Match | None:
    """The first fill the observations allow, or None. Order of input does not matter."""
    ordered = sorted(
        (o for o in observations if _usable(order, o)), key=lambda o: (o.time, o.source)
    )
    for observation in ordered:
        if observation.source == "minute_bar":
            price = _match_bar(order, observation, limit_down, limit_up, slippage_ticks)
        else:
            price = _match_point(order, observation.price, limit_down, limit_up, slippage_ticks)
        if price is not None:
            return Match(price=to_price(price), time=observation.time, source=observation.source)
    return None


# --- fill bookkeeping --------------------------------------------------------------


def day_trade_shares(sell_shares: int, *, bought_today: int, sold_today: int) -> int:
    """How many of a sell are 現股當沖: shares bought today and not yet sold today."""
    return max(0, min(sell_shares, bought_today - sold_today))


def compute_fill(
    side: Side,
    shares: int,
    price: Decimal,
    *,
    symbol: str,
    commission_discount: Decimal | float = 1,
    day_trade_qty: int = 0,
    trade_date: date | None = None,
) -> FillAmounts:
    """Amount, commission and tax of one fill (whole NT$).

    ``day_trade_qty`` of the sold shares are taxed at the day-trade rate (stocks only;
    ETFs keep their own rate inside ``transaction_tax``), the rest at the normal rate.
    """
    config = cost_config(commission_discount)
    amount = to_money(D(shares) * price)
    fee = commission(amount, config)
    if side == "buy":
        return FillAmounts(amount, fee, 0, False)
    qty = min(max(day_trade_qty, 0), shares)
    etf = is_etf(symbol)
    if qty == 0:
        tax = transaction_tax(
            amount, symbol=symbol, etf=etf, trade_date=trade_date, config=config
        )
    elif qty == shares:
        tax = transaction_tax(
            amount, symbol=symbol, etf=etf, day_trade=True, trade_date=trade_date, config=config
        )
    else:
        part = to_money(D(amount) * D(qty) / D(shares))
        tax = transaction_tax(
            part, symbol=symbol, etf=etf, day_trade=True, trade_date=trade_date, config=config
        ) + transaction_tax(
            amount - part, symbol=symbol, etf=etf, trade_date=trade_date, config=config
        )
    return FillAmounts(amount, fee, tax, qty > 0)


def lots_to_shares(lots: int) -> int:
    return lots * BOARD_LOT
