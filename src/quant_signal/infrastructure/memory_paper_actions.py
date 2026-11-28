"""In-memory twin of ``db/paper_actions_repository.py`` (tests and demo mode).

Mirrors the Postgres constraints: the per-event unique key, the one-posting-per-action
ledger key and the column lengths. Every method runs without awaiting, so on one event
loop each is atomic.
"""

from __future__ import annotations

from uuid import UUID

from quant_signal.domain.paper_actions import CorporateActionRecord, dividend_ledger_entry

_MAX_LENGTH = {"symbol": 32, "event_kind": 20, "source": 32}


class MemoryPaperActionsMixin:
    @property
    def paper_actions(self) -> dict[UUID, CorporateActionRecord]:
        return self.__dict__.setdefault("_paper_actions", {})

    async def apply_paper_corporate_action(self, action: CorporateActionRecord) -> bool:
        for column, limit in _MAX_LENGTH.items():
            if len(getattr(action, column)) > limit:
                raise ValueError(f"value too long for {column} (max {limit})")
        if action.entitled_shares <= 0 or action.cash_amount < 0 or action.shares_added < 0:
            raise ValueError("check constraint violated: paper corporate action payout")
        if action.cash_amount <= 0 and action.shares_added <= 0:
            raise ValueError("check constraint violated: paper corporate action payout")
        account = self.paper.accounts.get(action.account_id)  # type: ignore[attr-defined]
        if account is None or account.archived or account.epoch != action.epoch:
            return False
        key = (action.account_id, action.epoch, action.symbol, action.ex_date)
        if any(
            (a.account_id, a.epoch, a.symbol, a.ex_date) == key for a in self.paper_actions.values()
        ):
            return False
        self.paper_actions[action.id] = action.model_copy()
        entry = dividend_ledger_entry(action)
        if entry is not None:
            self.paper.ledger.append(entry)  # type: ignore[attr-defined]
        return True

    async def list_paper_corporate_actions(
        self, account_id: UUID, *, epoch: int | None
    ) -> list[CorporateActionRecord]:
        found = [
            a.model_copy()
            for a in self.paper_actions.values()
            if a.account_id == account_id and (epoch is None or a.epoch == epoch)
        ]
        return sorted(found, key=lambda a: (a.ex_at, a.symbol, str(a.id)))

    async def list_paper_traded_pairs(self) -> list[tuple[UUID, str]]:
        pairs = set()
        for fill in self.paper.fills.values():  # type: ignore[attr-defined]
            account = self.paper.accounts.get(fill.account_id)  # type: ignore[attr-defined]
            if account is not None and not account.archived and account.epoch == fill.epoch:
                pairs.add((fill.account_id, fill.symbol))
        return sorted(pairs)
