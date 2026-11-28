"""PostgreSQL storage for paper-trading dividends, mixed into ``PostgresQuantRepository``."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from quant_signal.domain.paper_actions import (
    CorporateActionRecord,
    DividendCheckRecord,
    dividend_ledger_entry,
)
from quant_signal.infrastructure.db.paper_models import (
    PaperAccountRow,
    PaperCorporateActionRow,
    PaperDividendCheckRow,
    PaperFillRow,
    PaperLedgerRow,
)

_ACTION_COLUMNS = (
    "id account_id epoch symbol ex_date event_kind entitled_shares cash_dividend_per_share "
    "stock_dividend_per_share cash_amount shares_added ex_at applied_at source "
    "source_revision source_available_at revalued_at"
).split()


def _action(row: PaperCorporateActionRow) -> CorporateActionRecord:
    return CorporateActionRecord(**{name: getattr(row, name) for name in _ACTION_COLUMNS})


class PaperActionsRepositoryMixin:
    """Expects ``self.session`` (an ``AsyncSession``). Writers commit their own work."""

    async def apply_paper_corporate_action(self, action: CorporateActionRecord) -> bool:
        """Record the action and its cash posting in ONE transaction; False if it exists.

        The unique key ``(account, epoch, symbol, ex_date)`` is the idempotency guard
        (``ON CONFLICT DO NOTHING``), and the ledger row carries ``action_id`` under the
        unique key ``(action_id, kind)``, so even two concurrent writers, a retry after a
        crash or a restart can never post a dividend twice. The account row lock only
        orders the writers against resets (the epoch must still be current) and fills.
        """
        account = await self.session.get(
            PaperAccountRow, action.account_id, with_for_update=True, populate_existing=True
        )
        if account is None or account.archived or account.epoch != action.epoch:
            await self.session.commit()
            return False
        try:
            inserted = await self.session.execute(
                insert(PaperCorporateActionRow)
                .values(**action.model_dump())
                .on_conflict_do_nothing(constraint="uq_paper_actions_event")
                .returning(PaperCorporateActionRow.id)
            )
            if inserted.scalar_one_or_none() is None:
                await self.session.commit()
                return False
            entry = dividend_ledger_entry(action)
            if entry is not None:
                self.session.add(PaperLedgerRow(**entry.model_dump()))
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            return False
        return True

    async def list_paper_corporate_actions(
        self, account_id: UUID, *, epoch: int | None
    ) -> list[CorporateActionRecord]:
        """Oldest first (by ex-date, then symbol); ``epoch=None`` returns every epoch."""
        statement = select(PaperCorporateActionRow).where(
            PaperCorporateActionRow.account_id == account_id
        )
        if epoch is not None:
            statement = statement.where(PaperCorporateActionRow.epoch == epoch)
        rows = await self.session.scalars(
            statement.order_by(
                PaperCorporateActionRow.ex_at,
                PaperCorporateActionRow.symbol,
                PaperCorporateActionRow.id,
            )
        )
        return [_action(row) for row in rows]

    async def list_active_paper_corporate_actions(self) -> list[CorporateActionRecord]:
        """Every action of every active account's current epoch (one query for the job)."""
        rows = await self.session.scalars(
            select(PaperCorporateActionRow)
            .join(
                PaperAccountRow,
                (PaperAccountRow.id == PaperCorporateActionRow.account_id)
                & (PaperAccountRow.epoch == PaperCorporateActionRow.epoch),
            )
            .where(PaperAccountRow.archived.is_(False))
            .order_by(
                PaperCorporateActionRow.account_id,
                PaperCorporateActionRow.ex_at,
                PaperCorporateActionRow.symbol,
                PaperCorporateActionRow.id,
            )
        )
        return [_action(row) for row in rows]

    async def list_paper_actions_to_revalue(self) -> list[CorporateActionRecord]:
        """Actions of active accounts' current epochs whose snapshots are not re-valued yet."""
        rows = await self.session.scalars(
            select(PaperCorporateActionRow)
            .join(
                PaperAccountRow,
                (PaperAccountRow.id == PaperCorporateActionRow.account_id)
                & (PaperAccountRow.epoch == PaperCorporateActionRow.epoch),
            )
            .where(
                PaperAccountRow.archived.is_(False),
                PaperCorporateActionRow.revalued_at.is_(None),
            )
            .order_by(PaperCorporateActionRow.ex_at, PaperCorporateActionRow.id)
        )
        return [_action(row) for row in rows]

    async def mark_paper_actions_revalued(self, action_ids: list[UUID], *, now: datetime) -> None:
        if not action_ids:
            return
        await self.session.execute(
            update(PaperCorporateActionRow)
            .where(PaperCorporateActionRow.id.in_(action_ids))
            .values(revalued_at=now)
        )
        await self.session.commit()

    async def record_paper_dividend_check(self, check: DividendCheckRecord) -> None:
        """Remember that an event revision was evaluated with nothing due (an upsert)."""
        values = check.model_dump()
        await self.session.execute(
            insert(PaperDividendCheckRow)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["account_id", "epoch", "symbol", "ex_date"],
                set_={
                    "source_revision": values["source_revision"],
                    "evaluated_at": values["evaluated_at"],
                },
            )
        )
        await self.session.commit()

    async def list_paper_dividend_checks(self) -> list[DividendCheckRecord]:
        """The checks of every active account's current epoch."""
        rows = await self.session.scalars(
            select(PaperDividendCheckRow)
            .join(
                PaperAccountRow,
                (PaperAccountRow.id == PaperDividendCheckRow.account_id)
                & (PaperAccountRow.epoch == PaperDividendCheckRow.epoch),
            )
            .where(PaperAccountRow.archived.is_(False))
        )
        return [
            DividendCheckRecord(
                account_id=row.account_id,
                epoch=row.epoch,
                symbol=row.symbol,
                ex_date=row.ex_date,
                source_revision=row.source_revision,
                evaluated_at=row.evaluated_at,
            )
            for row in rows
        ]

    async def list_paper_traded_pairs(self) -> list[tuple[UUID, str]]:
        """``(account_id, symbol)`` an active account has traded in its current epoch.

        One aggregate for the dividend job: only those accounts can be entitled to
        anything, including ones that sold out on the ex-date itself.
        """
        rows = await self.session.execute(
            select(PaperFillRow.account_id, PaperFillRow.symbol)
            .join(
                PaperAccountRow,
                (PaperAccountRow.id == PaperFillRow.account_id)
                & (PaperAccountRow.epoch == PaperFillRow.epoch),
            )
            .where(PaperAccountRow.archived.is_(False))
            .distinct()
        )
        return sorted((account_id, symbol) for account_id, symbol in rows)
