"""In-memory twin of ``db/paper_proposal_repository.py`` (tests and demo mode).

Every method runs without awaiting inside its body, so on one event loop each is atomic,
matching the row locks the Postgres version takes.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from quant_signal.domain.paper import PaperOrderRecord
from quant_signal.domain.paper_proposals import ProposalRecord
from quant_signal.infrastructure.column_limits import enforce_column_limits
from quant_signal.infrastructure.db.paper_models import PaperOrderRow, PaperProposalRow


class MemoryPaperProposalMixin:
    @property
    def paper_proposals(self) -> dict[UUID, ProposalRecord]:
        store = self.__dict__.get("_paper_proposals")
        if store is None:
            store = self.__dict__["_paper_proposals"] = {}
        return store

    async def release_read_transaction(self) -> None:
        """No transactions in memory: nothing to end."""

    async def add_paper_proposal(self, record: ProposalRecord) -> int:
        enforce_column_limits(PaperProposalRow, record.model_dump())
        superseded = 0
        for other in self.paper_proposals.values():
            if (
                other.account_id == record.account_id
                and other.symbol == record.symbol
                and other.status == "pending"
            ):
                other.status = "superseded"
                other.decided_at = record.created_at
                superseded += 1
        self.paper_proposals[record.id] = record.model_copy(deep=True)
        return superseded

    async def get_paper_proposal(self, proposal_id: UUID) -> ProposalRecord | None:
        record = self.paper_proposals.get(proposal_id)
        return None if record is None else record.model_copy(deep=True)

    async def list_paper_proposals(
        self,
        account_id: UUID,
        *,
        epoch: int,
        statuses: list[str] | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ProposalRecord], int]:
        found = [
            p
            for p in self.paper_proposals.values()
            if p.account_id == account_id
            and p.epoch == epoch
            and (statuses is None or p.status in statuses)
        ]
        found.sort(key=lambda p: (p.created_at, str(p.id)), reverse=True)
        return [p.model_copy(deep=True) for p in found[offset : offset + limit]], len(found)

    async def expire_paper_proposals(self, account_id: UUID, *, now: datetime) -> int:
        count = 0
        for proposal in self.paper_proposals.values():
            if (
                proposal.account_id == account_id
                and proposal.status == "pending"
                and proposal.expires_at <= now
            ):
                proposal.status = "expired"
                proposal.decided_at = now
                count += 1
        return count

    def _pprop_expire_pending(self, account_id: UUID, now: datetime) -> None:
        for proposal in self.paper_proposals.values():
            if proposal.account_id == account_id and proposal.status == "pending":
                proposal.status = "expired"
                proposal.decided_at = now

    async def reject_paper_proposal(
        self, proposal_id: UUID, *, now: datetime, reason: str | None
    ) -> bool:
        proposal = self.paper_proposals.get(proposal_id)
        if proposal is None or proposal.status != "pending":
            return False
        proposal.status = "rejected"
        proposal.decided_at = now
        proposal.reject_reason = reason
        return True

    async def accept_paper_proposal(
        self, proposal_id: UUID, order: PaperOrderRecord, *, now: datetime
    ) -> bool:
        proposal = self.paper_proposals.get(proposal_id)
        account = self.paper.accounts.get(proposal.account_id) if proposal else None
        if (
            proposal is None
            or account is None
            or proposal.status != "pending"
            or proposal.expires_at <= now
            or proposal.epoch != account.epoch
        ):
            return False
        enforce_column_limits(PaperOrderRow, order.model_dump())
        self.paper.orders[order.id] = order.model_copy()
        proposal.status = "accepted"
        proposal.order_id = order.id
        proposal.decided_at = now
        return True
