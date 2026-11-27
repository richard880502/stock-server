"""Storage contract for paper trading (implemented by Postgres and in-memory twins).

The market-data reads paper trading needs (``latest_quotes``, ``list_minute_bars``,
``list_market_stock_rows``, ``list_market_trading_dates``, ``list_market_observations``,
``search_market_stocks``) already exist on ``QuantRepository``; the same repository object
serves both.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

from quant_signal.domain.paper import (
    LedgerEntry,
    PaperAccountRecord,
    PaperFillRecord,
    PaperOrderRecord,
    SnapshotRecord,
)

PaperOrderFinish = Literal["cancelled", "expired", "rejected"]


class PaperRepository(Protocol):
    async def create_paper_account(
        self, account: PaperAccountRecord, *, max_accounts: int
    ) -> Literal["created", "limit", "duplicate"]:
        """Insert the account and its opening deposit, atomically per user."""
        ...

    async def get_paper_account(self, account_id: UUID) -> PaperAccountRecord | None: ...

    async def lock_paper_account(self, account_id: UUID) -> None:
        """Serialise order placement per account until the next write commits."""
        ...

    async def list_paper_accounts(self, user_id: UUID) -> list[PaperAccountRecord]: ...

    async def list_active_paper_accounts(self) -> list[PaperAccountRecord]: ...

    async def update_paper_account(
        self,
        account_id: UUID,
        *,
        name: str | None = None,
        commission_discount: Decimal | None = None,
        allow_day_trade: bool | None = None,
    ) -> Literal["ok", "duplicate", "missing"]: ...

    async def reset_paper_account(
        self, account_id: UUID, *, now: datetime
    ) -> PaperAccountRecord | None:
        """Cancel working orders, start a new epoch and re-deposit ``initial_cash``."""
        ...

    async def archive_paper_account(self, account_id: UUID, *, now: datetime) -> bool: ...

    async def add_paper_order(self, order: PaperOrderRecord) -> None: ...

    async def get_paper_order(self, order_id: UUID) -> PaperOrderRecord | None: ...

    async def list_paper_orders(
        self,
        account_id: UUID,
        *,
        statuses: list[str] | None,
        epoch: int | None,
        limit: int,
        offset: int,
    ) -> tuple[list[PaperOrderRecord], int]:
        """Newest first, with the total count before paging."""
        ...

    async def list_working_paper_orders(
        self, account_id: UUID | None = None
    ) -> list[PaperOrderRecord]: ...

    async def finish_paper_order(
        self,
        order_id: UUID,
        *,
        status: PaperOrderFinish,
        now: datetime,
        reject_reason: str | None = None,
    ) -> bool:
        """working -> status and release the reserve; False if it was not working."""
        ...

    async def apply_paper_fill(
        self, fill: PaperFillRecord, ledger: list[LedgerEntry], *, now: datetime
    ) -> bool:
        """Book a fill: lock the order, require ``working``, insert the fill and ledger
        postings, mark it ``filled`` and release the reserve, all in one transaction.
        False (nothing written) when the order is not working any more."""
        ...

    async def list_paper_fills(
        self, account_id: UUID, *, epoch: int | None
    ) -> list[PaperFillRecord]:
        """Oldest first; ``epoch=None`` returns every epoch."""
        ...

    async def paper_cash(self, account_id: UUID, epoch: int) -> int: ...

    async def list_paper_ledger(self, account_id: UUID, epoch: int) -> list[LedgerEntry]: ...

    async def upsert_paper_snapshot(self, snapshot: SnapshotRecord) -> None: ...

    async def list_paper_snapshots(
        self, account_id: UUID, *, epoch: int, since: date | None = None
    ) -> list[SnapshotRecord]: ...
