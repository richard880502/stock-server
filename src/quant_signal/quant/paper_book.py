"""Positions, moving-average cost and realised P&L derived from fills (pure).

Nothing here is stored: cash and positions are always recomputed from the fills of the
account's current epoch, so a balance can never be edited in place.

Cost basis is kept in whole NT$ including buy commissions. A sell removes
``round_half_up(cost x sold / held)`` from it, and ``realised = net proceeds - removed``.
Because every term is an integer,

    cash - initial_cash == realised_total - remaining_cost_basis

holds exactly, so equity - initial_cash == realised + unrealised with no rounding drift.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
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


@dataclass
class PositionState:
    symbol: str
    shares: int = 0
    cost: int = 0  # total cost basis, buy commissions included
    bought_on: dict[date, int] = field(default_factory=dict)  # shares bought per Taipei date
    sold_on: dict[date, int] = field(default_factory=dict)  # shares sold per Taipei date

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


def replay(fills: Iterable[FillLike]) -> Book:
    """Apply fills in time order (ties broken by id) and return the resulting book."""
    book = Book()
    for fill in sorted(fills, key=lambda f: (f.filled_at, str(f.id))):
        position = book.positions.setdefault(fill.symbol, PositionState(fill.symbol))
        if fill.side == "buy":
            position.shares += fill.shares
            position.cost += fill.amount + fill.commission
            day = local_date(fill.filled_at)  # type: ignore[arg-type]
            position.bought_on[day] = position.bought_on.get(day, 0) + fill.shares
            continue
        sold_day = local_date(fill.filled_at)  # type: ignore[arg-type]
        position.sold_on[sold_day] = position.sold_on.get(sold_day, 0) + fill.shares
        sold = min(fill.shares, position.shares)
        if sold <= 0:
            book.realized_by_fill[fill.id] = 0
            continue
        removed = to_money(Decimal(position.cost) * Decimal(sold) / Decimal(position.shares))
        net = fill.amount - fill.commission - fill.tax
        realised = net - removed
        position.shares -= sold
        position.cost -= removed
        book.realized_by_fill[fill.id] = realised
        book.realized_total += realised
        if position.shares == 0:
            position.cost = 0
    return book
