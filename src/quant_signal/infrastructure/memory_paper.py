"""In-memory twin of ``db/paper_repository.py`` (tests and demo mode).

Every method runs without awaiting inside its body, so on one event loop each is atomic,
matching the row locks the Postgres version takes.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from quant_signal.application.paper_ports import PaperInsufficientCash
from quant_signal.domain.paper import (
    LedgerEntry,
    PaperAccountRecord,
    PaperFillRecord,
    PaperOrderRecord,
    SnapshotRecord,
)
from quant_signal.domain.tw_trading import TWCostConfig, commission


class _PaperState:
    def __init__(self) -> None:
        self.accounts: dict[UUID, PaperAccountRecord] = {}
        self.orders: dict[UUID, PaperOrderRecord] = {}
        self.fills: dict[UUID, PaperFillRecord] = {}
        self.fill_by_order: dict[UUID, UUID] = {}
        self.ledger: list[LedgerEntry] = []
        self.equity_rows: dict[tuple[UUID, int, date], SnapshotRecord] = {}


class MemoryPaperMixin:
    @property
    def paper(self) -> _PaperState:
        state = self.__dict__.get("_paper_state")
        if state is None:
            state = self.__dict__["_paper_state"] = _PaperState()
        return state

    async def create_paper_account(
        self, account: PaperAccountRecord, *, max_accounts: int
    ) -> Literal["created", "limit", "duplicate"]:
        live = [
            a
            for a in self.paper.accounts.values()
            if a.user_id == account.user_id and not a.archived
        ]
        if len(live) >= max_accounts:
            return "limit"
        if any(a.name == account.name for a in live):
            return "duplicate"
        self.paper.accounts[account.id] = account.model_copy()
        self.paper.ledger.append(
            LedgerEntry(
                account_id=account.id,
                epoch=account.epoch,
                at=account.created_at,
                kind="deposit",
                amount=account.initial_cash,
                note="開戶入金",
            )
        )
        return "created"

    async def get_paper_account(self, account_id: UUID) -> PaperAccountRecord | None:
        account = self.paper.accounts.get(account_id)
        return None if account is None else account.model_copy()

    async def lock_paper_account(self, account_id: UUID) -> None:
        return None

    async def list_paper_accounts(self, user_id: UUID) -> list[PaperAccountRecord]:
        found = [
            a.model_copy()
            for a in self.paper.accounts.values()
            if a.user_id == user_id and not a.archived
        ]
        return sorted(found, key=lambda a: (a.created_at, str(a.id)))

    async def list_active_paper_accounts(self) -> list[PaperAccountRecord]:
        return [a.model_copy() for a in self.paper.accounts.values() if not a.archived]

    async def update_paper_account(
        self,
        account_id: UUID,
        *,
        name: str | None = None,
        commission_discount: Decimal | None = None,
        allow_day_trade: bool | None = None,
    ) -> Literal["ok", "duplicate", "missing", "insufficient"]:
        account = self.paper.accounts.get(account_id)
        if account is None or account.archived:
            return "missing"
        if name is not None and name != account.name:
            if any(
                a.name == name and a.user_id == account.user_id and not a.archived
                for a in self.paper.accounts.values()
            ):
                return "duplicate"
            account.name = name
        if commission_discount is not None and commission_discount != account.commission_discount:
            orders = [
                o
                for o in self.paper.orders.values()
                if o.account_id == account_id and o.status == "working" and o.side == "buy"
            ]
            config = TWCostConfig(commission_discount=commission_discount)
            resized = {
                o.id: o.reserve_amount + commission(o.reserve_amount, config)
                for o in orders
                if o.reserve_amount
            }
            cash = await self.paper_cash(account_id, account.epoch)
            others = sum(o.reserved_cash for o in orders if o.id not in resized)
            if cash - others - sum(resized.values()) < 0:
                return "insufficient"
            for order in orders:
                if order.id in resized:
                    order.reserved_cash = resized[order.id]
            account.commission_discount = commission_discount
        if allow_day_trade is not None:
            account.allow_day_trade = allow_day_trade
        return "ok"

    def _cancel_working(self, account_id: UUID, now: datetime) -> None:
        for order in self.paper.orders.values():
            if order.account_id == account_id and order.status == "working":
                order.status = "cancelled"
                order.reserved_cash = 0
                order.updated_at = now

    async def reset_paper_account(
        self, account_id: UUID, *, now: datetime
    ) -> PaperAccountRecord | None:
        account = self.paper.accounts.get(account_id)
        if account is None or account.archived:
            return None
        self._cancel_working(account_id, now)
        self._pprop_expire_pending(account_id, now)
        account.epoch += 1
        account.reset_at = now
        self.paper.ledger.append(
            LedgerEntry(
                account_id=account_id,
                epoch=account.epoch,
                at=now,
                kind="reset",
                amount=account.initial_cash,
                note="重設帳戶",
            )
        )
        return account.model_copy()

    async def archive_paper_account(self, account_id: UUID, *, now: datetime) -> bool:
        account = self.paper.accounts.get(account_id)
        if account is None or account.archived:
            return False
        self._cancel_working(account_id, now)
        account.archived = True
        return True

    async def add_paper_order(self, order: PaperOrderRecord) -> None:
        self.paper.orders[order.id] = order.model_copy()

    async def get_paper_order(self, order_id: UUID) -> PaperOrderRecord | None:
        order = self.paper.orders.get(order_id)
        return None if order is None else order.model_copy()

    async def list_paper_orders(
        self,
        account_id: UUID,
        *,
        statuses: list[str] | None,
        epoch: int | None,
        limit: int,
        offset: int,
    ) -> tuple[list[PaperOrderRecord], int]:
        found = [
            o
            for o in self.paper.orders.values()
            if o.account_id == account_id
            and (statuses is None or o.status in statuses)
            and (epoch is None or o.epoch == epoch)
        ]
        found.sort(key=lambda o: (o.created_at, str(o.id)), reverse=True)
        return [o.model_copy() for o in found[offset : offset + limit]], len(found)

    async def list_working_paper_orders(
        self, account_id: UUID | None = None
    ) -> list[PaperOrderRecord]:
        found = [
            o.model_copy()
            for o in self.paper.orders.values()
            if o.status == "working" and (account_id is None or o.account_id == account_id)
        ]
        return sorted(found, key=lambda o: (o.created_at, str(o.id)))

    async def finish_paper_order(
        self,
        order_id: UUID,
        *,
        status: str,
        now: datetime,
        reject_reason: str | None = None,
    ) -> bool:
        order = self.paper.orders.get(order_id)
        if order is None or order.status != "working":
            return False
        order.status = status  # type: ignore[assignment]
        order.reserved_cash = 0
        order.updated_at = now
        if reject_reason is not None:
            order.reject_reason = reject_reason
        return True

    async def apply_paper_fill(
        self, fill: PaperFillRecord, ledger: list[LedgerEntry], *, now: datetime
    ) -> bool:
        order = self.paper.orders.get(fill.order_id)
        if (
            order is None
            or order.status != "working"
            or order.epoch != fill.epoch
            or fill.order_id in self.paper.fill_by_order
        ):
            return False
        account = self.paper.accounts.get(fill.account_id)
        if account is None or account.epoch != fill.epoch:
            return False
        if fill.side == "buy":
            cash = sum(
                e.amount
                for e in self.paper.ledger
                if e.account_id == fill.account_id and e.epoch == fill.epoch
            )
            others = sum(
                o.reserved_cash
                for o in self.paper.orders.values()
                if o.account_id == fill.account_id
                and o.status == "working"
                and o.id != fill.order_id
            )
            if cash - others < fill.amount + fill.commission:
                raise PaperInsufficientCash
        self.paper.fills[fill.id] = fill.model_copy()
        self.paper.fill_by_order[fill.order_id] = fill.id
        posted = {(e.fill_id, e.kind) for e in self.paper.ledger if e.fill_id is not None}
        for entry in ledger:
            if (entry.fill_id, entry.kind) not in posted:
                self.paper.ledger.append(entry.model_copy())
        order.status = "filled"
        order.reserved_cash = 0
        order.updated_at = now
        return True

    async def list_paper_fills(
        self, account_id: UUID, *, epoch: int | None
    ) -> list[PaperFillRecord]:
        found = [
            f.model_copy()
            for f in self.paper.fills.values()
            if f.account_id == account_id and (epoch is None or f.epoch == epoch)
        ]
        return sorted(found, key=lambda f: (f.filled_at, str(f.id)))

    async def paper_cash(self, account_id: UUID, epoch: int) -> int:
        return sum(
            e.amount for e in self.paper.ledger if e.account_id == account_id and e.epoch == epoch
        )

    async def paper_cash_and_working(
        self, account_id: UUID, epoch: int
    ) -> tuple[int, list[PaperOrderRecord]]:
        working = await self.list_working_paper_orders(account_id)
        return await self.paper_cash(account_id, epoch), [o for o in working if o.epoch == epoch]

    async def list_open_position_symbols(self) -> list[str]:
        net: dict[tuple[UUID, str], int] = {}
        for fill in self.paper.fills.values():
            account = self.paper.accounts.get(fill.account_id)
            if account is None or account.archived or account.epoch != fill.epoch:
                continue
            key = (fill.account_id, fill.symbol)
            net[key] = net.get(key, 0) + (fill.shares if fill.side == "buy" else -fill.shares)
        for action in self.__dict__.get("_paper_actions", {}).values():
            account = self.paper.accounts.get(action.account_id)
            if account is None or account.archived or account.epoch != action.epoch:
                continue
            key = (action.account_id, action.symbol)
            net[key] = net.get(key, 0) + action.shares_added
        return sorted({symbol for (_, symbol), shares in net.items() if shares > 0})

    async def list_paper_ledger(self, account_id: UUID, epoch: int) -> list[LedgerEntry]:
        return [
            e.model_copy()
            for e in self.paper.ledger
            if e.account_id == account_id and e.epoch == epoch
        ]

    async def upsert_paper_snapshot(self, snapshot: SnapshotRecord) -> None:
        key = (snapshot.account_id, snapshot.epoch, snapshot.trading_date)
        self.paper.equity_rows[key] = snapshot.model_copy()

    async def list_paper_snapshots(
        self, account_id: UUID, *, epoch: int, since: date | None = None
    ) -> list[SnapshotRecord]:
        found = [
            s.model_copy()
            for (acct, snap_epoch, day), s in self.paper.equity_rows.items()
            if acct == account_id and snap_epoch == epoch and (since is None or day >= since)
        ]
        return sorted(found, key=lambda s: s.trading_date)
