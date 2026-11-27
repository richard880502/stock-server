"""Moving-average cost and realised P&L from fills, with hand-computed numbers."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from paper_support import FRI, MON, at

from quant_signal.domain.paper import PaperFillRecord
from quant_signal.quant.paper_book import replay
from quant_signal.quant.paper_matching import compute_fill

D = Decimal


def _fill(side, shares, price, when, symbol="1101.TW") -> PaperFillRecord:
    parts = compute_fill(side, shares, D(price), symbol=symbol)
    return PaperFillRecord(
        order_id=uuid4(),
        account_id=uuid4(),
        symbol=symbol,
        side=side,
        shares=shares,
        price=D(price),
        amount=parts.amount,
        commission=parts.commission,
        tax=parts.tax,
        filled_at=when,
        price_source="tick",
        price_time=when,
    )


def test_moving_average_over_several_buys_and_partial_sells() -> None:
    buy1 = _fill("buy", 2000, "100", at(FRI, 10))  # 200,000 + 285 -> cost 200,285
    buy2 = _fill("buy", 1000, "110", at(FRI, 11))  # 110,000 + 156 -> cost 110,156
    sell1 = _fill("sell", 1000, "120", at(MON, 10))  # 120,000; fee 171; tax 360
    sell2 = _fill("sell", 1000, "90", at(MON, 11))  # 90,000; fee 128; tax 270
    book = replay([sell2, buy2, sell1, buy1])  # input order is irrelevant
    state = book.positions["1101.TW"]
    # after buys: 3,000 sh, cost 310,441. sell 1: removes round(310,441/3) = 103,480
    assert book.realized_by_fill[sell1.id] == (120_000 - 171 - 360) - 103_480 == 15_989
    # left 2,000 sh at cost 206,961; sell 2 removes round(206,961 / 2) = 103,481 (half up)
    assert book.realized_by_fill[sell2.id] == (90_000 - 128 - 270) - 103_481 == -13_879
    assert (state.shares, state.cost) == (1000, 103_480)
    assert book.realized_total == 15_989 - 13_879
    assert book.held("1101.TW") == 1000 and book.bought_on("1101.TW", FRI) == 3000
    assert book.sold_on("1101.TW", MON) == 2000


def test_fully_sold_position_leaves_no_cost() -> None:
    buy = _fill("buy", 1000, "50", at(FRI, 10))  # 50,000 + 71 (71.25)
    sell = _fill("sell", 1000, "50", at(FRI, 11))  # 50,000 - 71 - 150
    book = replay([buy, sell])
    assert book.open_positions() == []
    assert book.realized_total == -(71 + 71 + 150) == -292
