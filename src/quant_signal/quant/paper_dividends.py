"""Dividend arithmetic for paper accounts (pure, no I/O).

The ex-date entitlement is the book as it stood at the end of the last session before the
ex-date: fills with ``filled_at`` before the ex-date's 09:00 open, plus every earlier
corporate action. Shares bought on the ex-date are not entitled; shares sold on it still are.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_FLOOR, Decimal

from quant_signal.quant.paper_book import ActionLike, BookCursor, FillLike, replay
from quant_signal.quant.paper_calendar import SESSION_OPEN, TAIPEI

# Corporate actions are booked at 08:30 Taipei on the ex-date: after the pre-open job window
# opens, before the first possible fill (09:00), so the book sees the new shares and the
# ledger the cash before anything trades that day.
EX_TIME = time(8, 30)


def ex_at(ex_date: date) -> datetime:
    return datetime.combine(ex_date, EX_TIME, TAIPEI)


def entitlement_cutoff(ex_date: date) -> datetime:
    """Fills strictly before this instant count towards the entitlement."""
    return datetime.combine(ex_date, SESSION_OPEN, TAIPEI)


def _dec(value: float | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True)
class Payout:
    entitled: int
    cash_amount: int
    shares_added: int
    cash_per_share: Decimal | None
    stock_per_share: Decimal | None


def entitled_shares(
    fills: Iterable[FillLike], actions: Iterable[ActionLike], symbol: str, ex_date: date
) -> int:
    cutoff = entitlement_cutoff(ex_date)
    booked_at = ex_at(ex_date)
    book = replay(
        [f for f in fills if f.filled_at < cutoff],  # type: ignore[operator]
        [a for a in actions if a.ex_at < booked_at],
    )
    return book.held(symbol)


def entitled_at(cursor: BookCursor, symbol: str, ex_date: date) -> int:
    """``entitled_shares`` from a cursor over the same fills and actions; call it with
    non-decreasing ex-dates so one walk through the history serves every event."""
    book = cursor.advance(fills_before=entitlement_cutoff(ex_date), actions_before=ex_at(ex_date))
    return book.held(symbol)


def payout(
    entitled: int, cash_per_share: float | Decimal | None, stock_per_share: float | Decimal | None
) -> Payout | None:
    """Cash rounded down to whole NT$, bonus shares rounded down; None when nothing is due."""
    cash_rate, stock_rate = _dec(cash_per_share), _dec(stock_per_share)
    cash = (
        int((Decimal(entitled) * cash_rate).to_integral_value(ROUND_FLOOR))
        if cash_rate is not None and cash_rate > 0
        else 0
    )
    added = (
        int((Decimal(entitled) * stock_rate).to_integral_value(ROUND_FLOOR))
        if stock_rate is not None and stock_rate > 0
        else 0
    )
    if entitled <= 0 or (cash <= 0 and added <= 0):
        return None
    return Payout(entitled, cash, added, cash_rate, stock_rate)
