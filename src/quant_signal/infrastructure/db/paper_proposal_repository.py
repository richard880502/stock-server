"""PostgreSQL storage for AI trade proposals, mixed into ``PostgresQuantRepository``."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, update

from quant_signal.domain.paper import PaperOrderRecord
from quant_signal.domain.paper_proposals import ProposalRecord
from quant_signal.infrastructure.db.paper_models import (
    PaperAccountRow,
    PaperOrderRow,
    PaperProposalRow,
)


def _pprop_record(row: PaperProposalRow) -> ProposalRecord:
    return ProposalRecord(
        id=row.id,
        user_id=row.user_id,
        account_id=row.account_id,
        epoch=row.epoch,
        symbol=row.symbol,
        name=row.name,
        as_of=row.as_of,
        created_at=row.created_at,
        expires_at=row.expires_at,
        status=row.status,  # type: ignore[arg-type]
        action=row.action,  # type: ignore[arg-type]
        side=row.side,  # type: ignore[arg-type]
        limit_price=None if row.limit_price is None else Decimal(row.limit_price),
        lots=row.lots,
        order_id=row.order_id,
        decided_at=row.decided_at,
        reject_reason=row.reject_reason,
        payload=dict(row.payload),
    )


class PaperProposalRepositoryMixin:
    """Expects ``self.session``; every writer commits (or rolls back) its own work."""

    async def release_read_transaction(self) -> None:
        """End the session's open (read) transaction, e.g. before a long model call.

        Commits rather than rolls back so anything a caller wrote on the way (an
        auto-sync) is kept; a no-op when no transaction is open.
        """
        if self.session.in_transaction():
            await self.session.commit()

    async def add_paper_proposal(self, record: ProposalRecord) -> int:
        await self.session.execute(
            select(PaperAccountRow.id)
            .where(PaperAccountRow.id == record.account_id)
            .with_for_update()
        )
        result = await self.session.execute(
            update(PaperProposalRow)
            .where(
                PaperProposalRow.account_id == record.account_id,
                PaperProposalRow.symbol == record.symbol,
                PaperProposalRow.status == "pending",
            )
            .values(status="superseded", decided_at=record.created_at)
        )
        self.session.add(PaperProposalRow(**record.model_dump()))
        await self.session.commit()
        return int(result.rowcount or 0)

    async def get_paper_proposal(self, proposal_id: UUID) -> ProposalRecord | None:
        row = await self.session.get(PaperProposalRow, proposal_id, populate_existing=True)
        return None if row is None else _pprop_record(row)

    async def list_paper_proposals(
        self,
        account_id: UUID,
        *,
        epoch: int,
        statuses: list[str] | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ProposalRecord], int]:
        filters = [PaperProposalRow.account_id == account_id, PaperProposalRow.epoch == epoch]
        if statuses is not None:
            filters.append(PaperProposalRow.status.in_(statuses))
        total = await self.session.scalar(
            select(func.count()).select_from(PaperProposalRow).where(*filters)
        )
        rows = await self.session.scalars(
            select(PaperProposalRow)
            .where(*filters)
            .order_by(PaperProposalRow.created_at.desc(), PaperProposalRow.id)
            .limit(limit)
            .offset(offset)
            .execution_options(populate_existing=True)
        )
        return [_pprop_record(row) for row in rows], int(total or 0)

    async def expire_paper_proposals(self, account_id: UUID, *, now: datetime) -> int:
        result = await self.session.execute(
            update(PaperProposalRow)
            .where(
                PaperProposalRow.account_id == account_id,
                PaperProposalRow.status == "pending",
                PaperProposalRow.expires_at <= now,
            )
            .values(status="expired", decided_at=now)
        )
        await self.session.commit()
        return int(result.rowcount or 0)

    async def _pprop_expire_pending(self, account_id: UUID, now: datetime) -> None:
        """Called by ``reset_paper_account`` inside its transaction (no commit here)."""
        await self.session.execute(
            update(PaperProposalRow)
            .where(PaperProposalRow.account_id == account_id, PaperProposalRow.status == "pending")
            .values(status="expired", decided_at=now)
        )

    async def reject_paper_proposal(
        self, proposal_id: UUID, *, now: datetime, reason: str | None
    ) -> bool:
        result = await self.session.execute(
            update(PaperProposalRow)
            .where(PaperProposalRow.id == proposal_id, PaperProposalRow.status == "pending")
            .values(status="rejected", decided_at=now, reject_reason=reason)
        )
        await self.session.commit()
        return bool(result.rowcount)

    async def accept_paper_proposal(
        self, proposal_id: UUID, order: PaperOrderRecord, *, now: datetime
    ) -> bool:
        # The order goes in first (the foreign key needs it); the conditional UPDATE is the
        # gate. A second accepter blocks on the proposal row until the first commits, then
        # matches no row and rolls its order back, so exactly one order survives.
        self.session.add(PaperOrderRow(**order.model_dump()))
        await self.session.flush()
        current_epoch = (
            select(PaperAccountRow.epoch)
            .where(PaperAccountRow.id == PaperProposalRow.account_id)
            .scalar_subquery()
        )
        result = await self.session.execute(
            update(PaperProposalRow)
            .where(
                PaperProposalRow.id == proposal_id,
                PaperProposalRow.status == "pending",
                PaperProposalRow.expires_at > now,
                PaperProposalRow.epoch == current_epoch,
            )
            .values(status="accepted", order_id=order.id, decided_at=now)
        )
        if not result.rowcount:
            await self.session.rollback()
            return False
        await self.session.commit()
        return True
