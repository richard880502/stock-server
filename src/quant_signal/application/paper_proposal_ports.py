"""Storage contract for AI trade proposals (Postgres and in-memory twins)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from quant_signal.domain.paper import PaperOrderRecord
from quant_signal.domain.paper_proposals import ProposalRecord


class PaperProposalRepository(Protocol):
    async def add_paper_proposal(self, record: ProposalRecord) -> int:
        """Under the account lock: mark the account's older ``pending`` proposals for the
        same symbol ``superseded`` and insert ``record``, in one transaction. Returns how
        many were superseded."""
        ...

    async def get_paper_proposal(self, proposal_id: UUID) -> ProposalRecord | None: ...

    async def list_paper_proposals(
        self,
        account_id: UUID,
        *,
        epoch: int,
        statuses: list[str] | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ProposalRecord], int]:
        """One epoch of an account, newest first, with the total before paging."""
        ...

    async def expire_paper_proposals(self, account_id: UUID, *, now: datetime) -> int:
        """``pending`` with ``expires_at <= now`` become ``expired``."""
        ...

    async def reject_paper_proposal(
        self, proposal_id: UUID, *, now: datetime, reason: str | None
    ) -> bool:
        """pending -> rejected; False (nothing written) when it was not pending."""
        ...

    async def accept_paper_proposal(
        self, proposal_id: UUID, order: PaperOrderRecord, *, now: datetime
    ) -> bool:
        """In ONE transaction insert ``order`` and move the proposal pending -> accepted
        (``order_id`` set). False, with nothing written, when the proposal is not pending,
        is overdue at ``now`` or belongs to an older epoch of its account. Two concurrent
        calls for one proposal book exactly one order."""
        ...
