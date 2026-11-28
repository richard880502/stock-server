"""PostgreSQL storage for paper trading, mixed into ``PostgresQuantRepository``."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, case, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from quant_signal.application.paper_ports import PaperInsufficientCash
from quant_signal.domain.paper import (
    LedgerEntry,
    PaperAccountRecord,
    PaperFillRecord,
    PaperOrderRecord,
    SnapshotRecord,
)
from quant_signal.domain.tw_trading import TWCostConfig, commission
from quant_signal.infrastructure.db.paper_models import (
    PaperAccountRow,
    PaperCorporateActionRow,
    PaperFillRow,
    PaperLedgerRow,
    PaperOrderRow,
    PaperSnapshotRow,
)


def _account(row: PaperAccountRow) -> PaperAccountRecord:
    return PaperAccountRecord(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        initial_cash=row.initial_cash,
        commission_discount=Decimal(row.commission_discount),
        allow_day_trade=row.allow_day_trade,
        created_at=row.created_at,
        reset_at=row.reset_at,
        archived=row.archived,
        epoch=row.epoch,
    )


def _order(row: PaperOrderRow) -> PaperOrderRecord:
    return PaperOrderRecord(
        id=row.id,
        account_id=row.account_id,
        epoch=row.epoch,
        symbol=row.symbol,
        name=row.name,
        side=row.side,  # type: ignore[arg-type]
        order_type=row.order_type,  # type: ignore[arg-type]
        limit_price=None if row.limit_price is None else Decimal(row.limit_price),
        lots=row.lots,
        status=row.status,  # type: ignore[arg-type]
        source=row.source,  # type: ignore[arg-type]
        session_date=row.session_date,
        created_at=row.created_at,
        updated_at=row.updated_at,
        reject_reason=row.reject_reason,
        reserved_cash=row.reserved_cash,
        reserve_amount=row.reserve_amount,
        note=row.note,
    )


def _fill(row: PaperFillRow) -> PaperFillRecord:
    return PaperFillRecord(
        id=row.id,
        order_id=row.order_id,
        account_id=row.account_id,
        epoch=row.epoch,
        symbol=row.symbol,
        side=row.side,  # type: ignore[arg-type]
        shares=row.shares,
        price=Decimal(row.price),
        amount=row.amount,
        commission=row.commission,
        tax=row.tax,
        day_trade=row.day_trade,
        filled_at=row.filled_at,
        price_source=row.price_source,  # type: ignore[arg-type]
        price_time=row.price_time,
    )


def _ledger(row: PaperLedgerRow) -> LedgerEntry:
    return LedgerEntry(
        id=row.id,
        account_id=row.account_id,
        epoch=row.epoch,
        at=row.at,
        kind=row.kind,  # type: ignore[arg-type]
        amount=row.amount,
        fill_id=row.fill_id,
        request_id=row.request_id,
        note=row.note,
    )


def _snapshot(row: PaperSnapshotRow) -> SnapshotRecord:
    return SnapshotRecord(
        account_id=row.account_id,
        trading_date=row.trading_date,
        epoch=row.epoch,
        cash=row.cash,
        market_value=row.market_value,
        equity=row.equity,
        realized_pnl_cum=row.realized_pnl_cum,
        benchmark_close=row.benchmark_close,
    )


class PaperRepositoryMixin:
    """Expects ``self.session`` (an ``AsyncSession``). Writers commit their own work."""

    async def create_paper_account(
        self, account: PaperAccountRecord, *, max_accounts: int
    ) -> Literal["created", "limit", "duplicate"]:
        # One creator per user at a time, so two requests cannot both pass the cap.
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"paper-accounts:{account.user_id}"},
        )
        live = (
            await self.session.execute(
                select(PaperAccountRow.id, PaperAccountRow.name).where(
                    PaperAccountRow.user_id == account.user_id, PaperAccountRow.archived.is_(False)
                )
            )
        ).all()
        if len(live) >= max_accounts:
            await self.session.commit()
            return "limit"
        if any(name == account.name for _, name in live):
            await self.session.commit()
            return "duplicate"
        self.session.add(
            PaperAccountRow(
                id=account.id,
                user_id=account.user_id,
                name=account.name,
                initial_cash=account.initial_cash,
                commission_discount=account.commission_discount,
                allow_day_trade=account.allow_day_trade,
                created_at=account.created_at,
                epoch=account.epoch,
            )
        )
        await self.session.flush()
        self.session.add(
            PaperLedgerRow(
                account_id=account.id,
                epoch=account.epoch,
                at=account.created_at,
                kind="deposit",
                amount=account.initial_cash,
                note="開戶入金",
            )
        )
        await self.session.commit()
        return "created"

    async def get_paper_account(self, account_id: UUID) -> PaperAccountRecord | None:
        # populate_existing: a lock taken since the last read may have waited for a writer
        # (reset, discount change); never answer from a stale identity-map copy.
        row = await self.session.get(PaperAccountRow, account_id, populate_existing=True)
        return None if row is None else _account(row)

    async def lock_paper_account(self, account_id: UUID) -> None:
        await self.session.execute(
            select(PaperAccountRow.id).where(PaperAccountRow.id == account_id).with_for_update()
        )

    async def list_paper_accounts(self, user_id: UUID) -> list[PaperAccountRecord]:
        rows = await self.session.scalars(
            select(PaperAccountRow)
            .where(PaperAccountRow.user_id == user_id, PaperAccountRow.archived.is_(False))
            .order_by(PaperAccountRow.created_at, PaperAccountRow.id)
        )
        return [_account(row) for row in rows]

    async def list_active_paper_accounts(self) -> list[PaperAccountRecord]:
        rows = await self.session.scalars(
            select(PaperAccountRow).where(PaperAccountRow.archived.is_(False))
        )
        return [_account(row) for row in rows]

    async def update_paper_account(
        self,
        account_id: UUID,
        *,
        name: str | None = None,
        commission_discount: Decimal | None = None,
        allow_day_trade: bool | None = None,
    ) -> Literal["ok", "duplicate", "missing", "insufficient"]:
        row = await self.session.get(
            PaperAccountRow, account_id, with_for_update=True, populate_existing=True
        )
        if row is None or row.archived:
            await self.session.commit()
            return "missing"
        if name is not None and name != row.name:
            clash = await self.session.scalar(
                select(PaperAccountRow.id).where(
                    PaperAccountRow.user_id == row.user_id,
                    PaperAccountRow.name == name,
                    PaperAccountRow.archived.is_(False),
                )
            )
            if clash is not None:
                await self.session.commit()
                return "duplicate"
            row.name = name
        if commission_discount is not None and commission_discount != row.commission_discount:
            # Re-size the reserves of working buys under the account lock; refuse the
            # change when the cash would no longer cover all of them.
            orders = (
                await self.session.scalars(
                    select(PaperOrderRow)
                    .where(
                        PaperOrderRow.account_id == account_id,
                        PaperOrderRow.status == "working",
                        PaperOrderRow.side == "buy",
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
            config = TWCostConfig(commission_discount=commission_discount)
            resized = {
                o.id: o.reserve_amount + commission(o.reserve_amount, config)
                for o in orders
                if o.reserve_amount
            }
            cash = await self.paper_cash(account_id, row.epoch)
            others = sum(o.reserved_cash for o in orders if o.id not in resized)
            if cash - others - sum(resized.values()) < 0:
                await self.session.rollback()
                return "insufficient"
            for order in orders:
                if order.id in resized:
                    order.reserved_cash = resized[order.id]
            row.commission_discount = commission_discount
        if allow_day_trade is not None:
            row.allow_day_trade = allow_day_trade
        await self.session.commit()
        return "ok"

    async def _cancel_working(self, account_id: UUID, now: datetime) -> None:
        await self.session.execute(
            update(PaperOrderRow)
            .where(PaperOrderRow.account_id == account_id, PaperOrderRow.status == "working")
            .values(status="cancelled", reserved_cash=0, updated_at=now)
        )

    async def reset_paper_account(
        self, account_id: UUID, *, now: datetime, initial_cash: int | None = None
    ) -> PaperAccountRecord | None:
        row = await self.session.get(
            PaperAccountRow, account_id, with_for_update=True, populate_existing=True
        )
        if row is None or row.archived:
            await self.session.commit()
            return None
        await self._cancel_working(account_id, now)
        await self._pprop_expire_pending(account_id, now)
        row.epoch += 1
        row.reset_at = now
        if initial_cash is not None:
            row.initial_cash = initial_cash
        self.session.add(
            PaperLedgerRow(
                account_id=account_id,
                epoch=row.epoch,
                at=now,
                kind="reset",
                amount=row.initial_cash,
                note="重設帳戶",
            )
        )
        await self.session.commit()
        return _account(row)

    async def archive_paper_account(self, account_id: UUID, *, now: datetime) -> bool:
        row = await self.session.get(
            PaperAccountRow, account_id, with_for_update=True, populate_existing=True
        )
        if row is None or row.archived:
            await self.session.commit()
            return False
        await self._cancel_working(account_id, now)
        row.archived = True
        await self.session.commit()
        return True

    async def add_paper_order(self, order: PaperOrderRecord) -> None:
        self.session.add(PaperOrderRow(**order.model_dump()))
        await self.session.commit()

    async def get_paper_order(self, order_id: UUID) -> PaperOrderRecord | None:
        row = await self.session.get(PaperOrderRow, order_id)
        return None if row is None else _order(row)

    async def list_paper_orders(
        self,
        account_id: UUID,
        *,
        statuses: list[str] | None,
        epoch: int | None,
        limit: int,
        offset: int,
    ) -> tuple[list[PaperOrderRecord], int]:
        filters = [PaperOrderRow.account_id == account_id]
        if statuses is not None:
            filters.append(PaperOrderRow.status.in_(statuses))
        if epoch is not None:
            filters.append(PaperOrderRow.epoch == epoch)
        total = await self.session.scalar(
            select(func.count()).select_from(PaperOrderRow).where(*filters)
        )
        rows = await self.session.scalars(
            select(PaperOrderRow)
            .where(*filters)
            .order_by(PaperOrderRow.created_at.desc(), PaperOrderRow.id)
            .limit(limit)
            .offset(offset)
        )
        return [_order(row) for row in rows], int(total or 0)

    async def list_working_paper_orders(
        self, account_id: UUID | None = None
    ) -> list[PaperOrderRecord]:
        statement = select(PaperOrderRow).where(PaperOrderRow.status == "working")
        if account_id is not None:
            statement = statement.where(PaperOrderRow.account_id == account_id)
        rows = await self.session.scalars(
            statement.order_by(PaperOrderRow.created_at, PaperOrderRow.id)
        )
        return [_order(row) for row in rows]

    async def finish_paper_order(
        self,
        order_id: UUID,
        *,
        status: str,
        now: datetime,
        reject_reason: str | None = None,
    ) -> bool:
        row = await self.session.get(
            PaperOrderRow, order_id, with_for_update=True, populate_existing=True
        )
        if row is None or row.status != "working":
            await self.session.commit()
            return False
        row.status = status
        row.reserved_cash = 0
        row.updated_at = now
        if reject_reason is not None:
            row.reject_reason = reject_reason
        await self.session.commit()
        return True

    async def apply_paper_fill(
        self, fill: PaperFillRecord, ledger: list[LedgerEntry], *, now: datetime
    ) -> bool:
        # The account lock serialises this with placement, resets and discount changes, so
        # the cash/reserve check below cannot be raced; the order row lock makes a second
        # worker (or a cancel) wait and the status check then turns it away. The unique
        # (order_id) and (fill_id, kind) constraints are the last line of defence.
        account = await self.session.get(
            PaperAccountRow, fill.account_id, with_for_update=True, populate_existing=True
        )
        row = await self.session.get(
            PaperOrderRow, fill.order_id, with_for_update=True, populate_existing=True
        )
        if (
            account is None
            or account.epoch != fill.epoch
            or row is None
            or row.status != "working"
            or row.epoch != fill.epoch
        ):
            await self.session.commit()
            return False
        if fill.side == "buy":
            cash = await self.paper_cash(fill.account_id, fill.epoch)
            others = await self.session.scalar(
                select(func.coalesce(func.sum(PaperOrderRow.reserved_cash), 0)).where(
                    PaperOrderRow.account_id == fill.account_id,
                    PaperOrderRow.status == "working",
                    PaperOrderRow.id != fill.order_id,
                )
            )
            if cash - int(others or 0) < fill.amount + fill.commission:
                await self.session.rollback()
                raise PaperInsufficientCash
        try:
            self.session.add(PaperFillRow(**fill.model_dump()))
            await self.session.flush()
            for entry in ledger:
                await self.session.execute(
                    insert(PaperLedgerRow)
                    .values(**entry.model_dump())
                    .on_conflict_do_nothing(constraint="uq_paper_ledger_fill_kind")
                )
            row.status = "filled"
            row.reserved_cash = 0
            row.updated_at = now
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            return False
        return True

    async def list_paper_fills(
        self, account_id: UUID, *, epoch: int | None
    ) -> list[PaperFillRecord]:
        statement = select(PaperFillRow).where(PaperFillRow.account_id == account_id)
        if epoch is not None:
            statement = statement.where(PaperFillRow.epoch == epoch)
        rows = await self.session.scalars(
            statement.order_by(PaperFillRow.filled_at, PaperFillRow.id)
        )
        return [_fill(row) for row in rows]

    async def paper_cash(self, account_id: UUID, epoch: int) -> int:
        total = await self.session.scalar(
            select(func.coalesce(func.sum(PaperLedgerRow.amount), 0)).where(
                PaperLedgerRow.account_id == account_id, PaperLedgerRow.epoch == epoch
            )
        )
        return int(total or 0)

    async def paper_cash_and_working(
        self, account_id: UUID, epoch: int
    ) -> tuple[int, list[PaperOrderRecord]]:
        # One statement, so cash and reserves come from the same snapshot.
        cash = (
            select(func.coalesce(func.sum(PaperLedgerRow.amount), 0).label("cash"))
            .where(PaperLedgerRow.account_id == account_id, PaperLedgerRow.epoch == epoch)
            .subquery()
        )
        result = await self.session.execute(
            select(cash.c.cash, PaperOrderRow)
            .select_from(cash)
            .outerjoin(
                PaperOrderRow,
                and_(
                    PaperOrderRow.account_id == account_id,
                    PaperOrderRow.status == "working",
                    PaperOrderRow.epoch == epoch,
                ),
            )
            .order_by(PaperOrderRow.created_at, PaperOrderRow.id)
            .execution_options(populate_existing=True)
        )
        rows = result.all()
        total = int(rows[0][0]) if rows else 0
        return total, [_order(order) for _, order in rows if order is not None]

    async def list_open_position_symbols(self) -> list[str]:
        """One aggregate for the demand registration: net shares per account and symbol
        (fills plus the bonus shares of stock dividends)."""
        signed = case((PaperFillRow.side == "buy", PaperFillRow.shares), else_=-PaperFillRow.shares)
        current = and_(
            PaperAccountRow.id == PaperFillRow.account_id,
            PaperAccountRow.epoch == PaperFillRow.epoch,
        )
        from_fills = (
            select(PaperFillRow.account_id, PaperFillRow.symbol, signed.label("n"))
            .join(PaperAccountRow, current)
            .where(PaperAccountRow.archived.is_(False))
        )
        from_actions = (
            select(
                PaperCorporateActionRow.account_id,
                PaperCorporateActionRow.symbol,
                PaperCorporateActionRow.shares_added.label("n"),
            )
            .join(
                PaperAccountRow,
                and_(
                    PaperAccountRow.id == PaperCorporateActionRow.account_id,
                    PaperAccountRow.epoch == PaperCorporateActionRow.epoch,
                ),
            )
            .where(PaperAccountRow.archived.is_(False))
        )
        union = from_fills.union_all(from_actions).subquery()
        rows = await self.session.execute(
            select(union.c.symbol)
            .group_by(union.c.account_id, union.c.symbol)
            .having(func.sum(union.c.n) > 0)
        )
        return sorted({symbol for (symbol,) in rows})

    async def list_paper_ledger(self, account_id: UUID, epoch: int) -> list[LedgerEntry]:
        rows = await self.session.scalars(
            select(PaperLedgerRow)
            .where(PaperLedgerRow.account_id == account_id, PaperLedgerRow.epoch == epoch)
            .order_by(PaperLedgerRow.at, PaperLedgerRow.id)
        )
        return [_ledger(row) for row in rows]

    async def upsert_paper_snapshot(self, snapshot: SnapshotRecord) -> None:
        values = snapshot.model_dump()
        statement = insert(PaperSnapshotRow).values(**values)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["account_id", "epoch", "trading_date"],
                set_={
                    k: getattr(statement.excluded, k)
                    for k in values
                    if k not in ("account_id", "epoch", "trading_date")
                },
            )
        )
        await self.session.commit()

    async def list_paper_snapshots(
        self, account_id: UUID, *, epoch: int, since: date | None = None
    ) -> list[SnapshotRecord]:
        statement = select(PaperSnapshotRow).where(
            PaperSnapshotRow.account_id == account_id, PaperSnapshotRow.epoch == epoch
        )
        if since is not None:
            statement = statement.where(PaperSnapshotRow.trading_date >= since)
        rows = await self.session.scalars(statement.order_by(PaperSnapshotRow.trading_date))
        return [_snapshot(row) for row in rows]
