"""Corporate actions on paper accounts (dividends): stored records and the REST contract.

Simplifications, stated once (the UI repeats them):

* The simulator credits the GROSS cash dividend on the ex-date. Real payment arrives weeks
  later, and payouts of NT$20,000 or more carry the 2.11% NHI supplementary premium and
  income tax. Booking on the ex-date keeps equity continuous across the price adjustment.
* Stock dividends add ``floor(shares x rate)`` shares at zero cost. Real life pays the
  fractional share in cash at par; the simulator drops it.
* Cash capital increases (現金增資), capital reductions and splits that do not arrive as
  ``stock_dividend_per_share`` are not modelled.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from quant_signal.domain.paper import LedgerEntry

DIVIDEND_NOTE = "現金股利（模擬以除息日全額入帳）"


class CorporateActionRecord(BaseModel):
    """One dividend event applied to one account's epoch."""

    id: UUID = Field(default_factory=uuid4)
    account_id: UUID
    epoch: int = 0
    symbol: str
    ex_date: date
    event_kind: str  # ex_dividend | ex_rights | ex_rights_dividend
    entitled_shares: int
    cash_dividend_per_share: Decimal | None = None
    stock_dividend_per_share: Decimal | None = None
    cash_amount: int = 0  # NT$, whole dollars, rounded down
    shares_added: int = 0
    ex_at: datetime  # when the book sees it: the ex-date 08:30 Taipei, before any fill of the day
    applied_at: datetime
    # Identity of the source event revision the figures came from.
    source: str
    source_revision: int = 1
    source_available_at: datetime


def dividend_ledger_entry(action: CorporateActionRecord) -> LedgerEntry | None:
    """The cash posting of an action (none for a pure stock dividend).

    Pointing the row at the action (``action_id``) lets the unique key
    ``(action_id, kind)`` make a second posting impossible in the database itself.
    """
    if action.cash_amount <= 0:
        return None
    return LedgerEntry(
        account_id=action.account_id,
        epoch=action.epoch,
        at=action.ex_at,
        kind="dividend",
        amount=action.cash_amount,
        action_id=action.id,
        note=DIVIDEND_NOTE,
    )


# --- responses ---------------------------------------------------------------------


class CorporateAction(BaseModel):
    id: UUID
    symbol: str
    name: str
    ex_date: date
    kind: str
    entitled_shares: int
    cash_amount: int
    shares_added: int
    cash_dividend_per_share: float | None
    stock_dividend_per_share: float | None
    applied_at: datetime


class CorporateActionList(BaseModel):
    actions: list[CorporateAction]
    has_more: bool
