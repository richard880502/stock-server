"""Positions, moving-average cost and realised P&L derived from fills (pure).

Nothing here is stored: cash and positions are always recomputed from the fills of the
account's current epoch, so a balance can never be edited in place.

Cost basis is kept in whole NT$ including buy commissions. A sell removes
``round_half_up(cost x sold / held)`` from it, and ``realised = net proceeds - removed``.
Because every term is an integer,

    cash - initial_cash == realised_total - remaining_cost_basis

holds exactly, so equity - initial_cash == realised + unrealised with no rounding drift.

Dividends are a second input stream, ordered with the fills by time (``ActionLike.ex_at``):
a cash dividend adds to ``dividend_income`` (its cash is a ledger row, not part of the
cost basis) and a stock dividend adds shares at zero cost, so the total cost basis is
unchanged and the average cost falls. With them the identity becomes

    cash - initial_cash == realised_total + dividend_income - remaining_cost_basis

Every caller must replay fills AND actions together (see ``application/paper_actions.load_book``).
"""

from __future__ import annotations

from bisect import bisect_left, insort
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from quant_signal.quant.paper_calendar import local_date
from quant_signal.quant.paper_matching import to_money


class FillLike(Protocol):
    id: UUID
    symbol: str
    side: str
    shares: int
    amount: int
    commission: int
    tax: int
    filled_at: object


class ActionLike(Protocol):
    id: UUID
    symbol: str
    ex_at: datetime
    cash_amount: int
    shares_added: int


@dataclass
class PositionState:
    symbol: str
    shares: int = 0
    cost: int = 0  # total cost basis, buy commissions included
    bought_on: dict[date, int] = field(default_factory=dict)  # shares bought per Taipei date
    sold_on: dict[date, int] = field(default_factory=dict)  # shares sold per Taipei date
    dividends: int = 0  # cash dividends received while this holding was open

    @property
    def avg_cost(self) -> Decimal:
        if not self.shares:
            return Decimal(0)
        return Decimal(self.cost) / Decimal(self.shares)


@dataclass
class Book:
    positions: dict[str, PositionState] = field(default_factory=dict)
    realized_by_fill: dict[UUID, int] = field(default_factory=dict)
    realized_total: int = 0
    dividend_income: int = 0  # all cash dividends credited, positions closed or not

    def held(self, symbol: str) -> int:
        position = self.positions.get(symbol)
        return position.shares if position else 0

    def bought_on(self, symbol: str, day: date) -> int:
        position = self.positions.get(symbol)
        return position.bought_on.get(day, 0) if position else 0

    def sold_on(self, symbol: str, day: date) -> int:
        position = self.positions.get(symbol)
        return position.sold_on.get(day, 0) if position else 0

    def open_positions(self) -> list[PositionState]:
        return sorted((p for p in self.positions.values() if p.shares > 0), key=lambda p: p.symbol)


def replay(fills: Iterable[FillLike], actions: Iterable[ActionLike] = ()) -> Book:
    """Apply fills and corporate actions in time order and return the resulting book.

    Ties: an action sorts before a fill at the same instant, then by id.
    """
    events = _ordered(fills, actions)
    book = Book()
    for _, order, _, item in events:
        _apply(book, order, item)
    return book


def _ordered(fills: Iterable[FillLike], actions: Iterable[ActionLike]) -> list[tuple]:
    events: list[tuple[object, int, str, object]] = [
        (f.filled_at, 1, str(f.id), f) for f in fills
    ] + [(a.ex_at, 0, str(a.id), a) for a in actions]
    events.sort(key=lambda e: (e[0], e[1], e[2]))  # type: ignore[type-var]
    return events


def _apply(book: Book, order: int, item: object) -> None:
    if order == 0:
        _apply_action(book, item)  # type: ignore[arg-type]
    else:
        _apply_fill(book, item)  # type: ignore[arg-type]


class BookCursor:
    """``replay`` walked forward in steps, for evaluating many dates against one history.

    ``advance`` applies, in ``replay``'s order, the leading items that fall before the
    bounds (fills before ``fills_before``, actions before ``actions_before``) and stops at
    the first that does not, so the book equals ``replay`` of exactly those items as long
    as no fill lies between an unapplied action and the fill bound (callers' bounds are
    the ex-date 08:30 and 09:00, and no fill is ever stamped before a session's 09:00).
    Bounds must not move backwards. ``add_action`` inserts a newly booked action; one
    that sorts before what was already applied rewinds the cursor (exact, just slower).
    """

    def __init__(self, fills: Iterable[FillLike], actions: Iterable[ActionLike] = ()) -> None:
        self._items = _ordered(fills, actions)
        self._next = 0
        self.book = Book()

    def add_action(self, action: ActionLike) -> None:
        item = (action.ex_at, 0, str(action.id), action)
        key = item[:3]
        index = bisect_left([e[:3] for e in self._items], key)  # type: ignore[type-var]
        insort(self._items, item, key=lambda e: e[:3])  # type: ignore[type-var]
        if index < self._next:  # behind the cursor: start over
            self._next = 0
            self.book = Book()

    def advance(self, *, fills_before: datetime, actions_before: datetime) -> Book:
        while self._next < len(self._items):
            when, order, _, item = self._items[self._next]
            if not when < (actions_before if order == 0 else fills_before):  # type: ignore[operator]
                break
            _apply(self.book, order, item)
            self._next += 1
        return self.book


def _apply_action(book: Book, action: ActionLike) -> None:
    position = book.positions.setdefault(action.symbol, PositionState(action.symbol))
    position.shares += action.shares_added  # zero cost: the total cost basis is unchanged
    position.dividends += action.cash_amount
    book.dividend_income += action.cash_amount


def _apply_fill(book: Book, fill: FillLike) -> None:
    position = book.positions.setdefault(fill.symbol, PositionState(fill.symbol))
    if fill.side == "buy":
        position.shares += fill.shares
        position.cost += fill.amount + fill.commission
        day = local_date(fill.filled_at)  # type: ignore[arg-type]
        position.bought_on[day] = position.bought_on.get(day, 0) + fill.shares
        return
    sold_day = local_date(fill.filled_at)  # type: ignore[arg-type]
    position.sold_on[sold_day] = position.sold_on.get(sold_day, 0) + fill.shares
    sold = min(fill.shares, position.shares)
    if sold <= 0:
        book.realized_by_fill[fill.id] = 0
        return
    removed = to_money(Decimal(position.cost) * Decimal(sold) / Decimal(position.shares))
    net = fill.amount - fill.commission - fill.tax
    realised = net - removed
    position.shares -= sold
    position.cost -= removed
    book.realized_by_fill[fill.id] = realised
    book.realized_total += realised
    if position.shares == 0:
        position.cost = 0
        position.dividends = 0
