"""In-memory twin of ``db/paper_transfers_repository.py`` (tests and demo mode).

Mirrors the Postgres rules: one posting per (account, request_id), the per-day cap, the
available-cash limit and the cash ceiling. Every method runs without awaiting, so on one
event loop each is atomic.
"""

from __future__ import annotations

from uuid import UUID

from quant_signal.application.paper_transfers import TransferOutcome, same_transfer
from quant_signal.domain.paper import TRANSFER_KINDS, LedgerEntry


class MemoryPaperTransferMixin:
    async def apply_paper_transfer(
        self,
        entry: LedgerEntry,
        *,
        day_start,
        day_end,
        max_per_day: int,
        max_cash: int,
    ) -> TransferOutcome:
        state = self.paper  # type: ignore[attr-defined]
        account = state.accounts.get(entry.account_id)
        if account is None or account.archived:
            return TransferOutcome("missing")
        for existing in state.ledger:
            if existing.account_id == entry.account_id and existing.request_id == entry.request_id:
                status = "replayed" if same_transfer(existing, entry) else "mismatch"
                return TransferOutcome(status, existing.model_copy())
        if account.epoch != entry.epoch:
            return TransferOutcome("stale")
        today = sum(
            1
            for e in state.ledger
            if e.account_id == entry.account_id
            and e.kind in TRANSFER_KINDS
            and day_start <= e.at < day_end
        )
        if today >= max_per_day:
            return TransferOutcome("daily_cap")
        cash = sum(
            e.amount
            for e in state.ledger
            if e.account_id == entry.account_id and e.epoch == entry.epoch
        )
        reserved = sum(
            o.reserved_cash
            for o in state.orders.values()
            if o.account_id == entry.account_id and o.status == "working"
        )
        available = cash - reserved
        if entry.amount < 0 and -entry.amount > available:
            return TransferOutcome("over_available", cash=cash, available=available)
        if entry.amount > 0 and cash + entry.amount > max_cash:
            return TransferOutcome("ceiling", cash=cash, available=available)
        state.ledger.append(entry.model_copy())
        return TransferOutcome("ok", entry, cash=cash + entry.amount, available=available)

    async def list_paper_transfer_entries(
        self, account_id: UUID, *, epoch: int | None
    ) -> list[LedgerEntry]:
        found = [
            e.model_copy()
            for e in self.paper.ledger  # type: ignore[attr-defined]
            if e.account_id == account_id
            and e.kind in TRANSFER_KINDS
            and (epoch is None or e.epoch == epoch)
        ]
        return sorted(found, key=lambda e: (e.at, str(e.id)))
