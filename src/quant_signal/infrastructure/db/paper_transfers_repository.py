"""PostgreSQL storage for paper-account deposits and withdrawals (migration 0027)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from quant_signal.application.paper_transfers import TransferOutcome, same_transfer
from quant_signal.domain.paper import TRANSFER_KINDS, LedgerEntry
from quant_signal.infrastructure.db.paper_models import (
    PaperAccountRow,
    PaperLedgerRow,
    PaperOrderRow,
)


def _ptx_entry(row: PaperLedgerRow) -> LedgerEntry:
    return LedgerEntry(
        id=row.id,
        account_id=row.account_id,
        epoch=row.epoch,
        at=row.at,
        kind=row.kind,  # type: ignore[arg-type]
        amount=row.amount,
        request_id=row.request_id,
        note=row.note,
    )


def _replay(original: LedgerEntry, entry: LedgerEntry) -> TransferOutcome:
    return TransferOutcome("replayed" if same_transfer(original, entry) else "mismatch", original)


class PaperTransferRepositoryMixin:
    """Expects ``self.session`` and ``PaperRepositoryMixin.paper_cash``."""

    async def _ptx_original(self, entry: LedgerEntry) -> LedgerEntry | None:
        row = await self.session.scalar(
            select(PaperLedgerRow)
            .where(
                PaperLedgerRow.account_id == entry.account_id,
                PaperLedgerRow.request_id == entry.request_id,
            )
            .execution_options(populate_existing=True)
        )
        return None if row is None else _ptx_entry(row)

    async def apply_paper_transfer(
        self,
        entry: LedgerEntry,
        *,
        day_start,
        day_end,
        max_per_day: int,
        max_cash: int,
    ) -> TransferOutcome:
        # The account row lock serialises transfers with order placement, fills, resets
        # and each other, so the daily count and the available cash cannot be raced.
        account = await self.session.get(
            PaperAccountRow, entry.account_id, with_for_update=True, populate_existing=True
        )
        if account is None or account.archived:
            await self.session.commit()
            return TransferOutcome("missing")
        original = await self._ptx_original(entry)
        if original is not None:
            await self.session.commit()
            return _replay(original, entry)
        if account.epoch != entry.epoch:
            await self.session.commit()
            return TransferOutcome("stale")
        today = await self.session.scalar(
            select(func.count())
            .select_from(PaperLedgerRow)
            .where(
                PaperLedgerRow.account_id == entry.account_id,
                PaperLedgerRow.kind.in_(TRANSFER_KINDS),
                PaperLedgerRow.at >= day_start,
                PaperLedgerRow.at < day_end,
            )
        )
        if int(today or 0) >= max_per_day:
            await self.session.commit()
            return TransferOutcome("daily_cap")
        cash = await self.paper_cash(entry.account_id, entry.epoch)
        reserved = await self.session.scalar(
            select(func.coalesce(func.sum(PaperOrderRow.reserved_cash), 0)).where(
                PaperOrderRow.account_id == entry.account_id, PaperOrderRow.status == "working"
            )
        )
        available = cash - int(reserved or 0)
        if entry.amount < 0 and -entry.amount > available:
            await self.session.commit()
            return TransferOutcome("over_available", cash=cash, available=available)
        if entry.amount > 0 and cash + entry.amount > max_cash:
            await self.session.commit()
            return TransferOutcome("ceiling", cash=cash, available=available)
        try:
            self.session.add(PaperLedgerRow(**entry.model_dump(exclude={"action_id", "fill_id"})))
            await self.session.commit()
        except IntegrityError:
            # The unique (account_id, request_id) is the last line of defence.
            await self.session.rollback()
            original = await self._ptx_original(entry)
            if original is None:
                raise
            return _replay(original, entry)
        return TransferOutcome("ok", entry, cash=cash + entry.amount, available=available)

    async def list_paper_transfer_entries(
        self, account_id: UUID, *, epoch: int | None
    ) -> list[LedgerEntry]:
        statement = select(PaperLedgerRow).where(
            PaperLedgerRow.account_id == account_id, PaperLedgerRow.kind.in_(TRANSFER_KINDS)
        )
        if epoch is not None:
            statement = statement.where(PaperLedgerRow.epoch == epoch)
        rows = await self.session.scalars(statement.order_by(PaperLedgerRow.at, PaperLedgerRow.id))
        return [_ptx_entry(row) for row in rows]
