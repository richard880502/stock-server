"""Deposits and withdrawals on a paper account (入金／出金), and time-weighted returns.

A transfer is a cash posting of kind ``transfer_in`` / ``transfer_out`` on the account's
ledger. It is NOT profit or loss: ``contributed_capital`` (initial cash + deposits -
withdrawals of the current epoch) replaces ``initial_cash`` as the cost base of
``total_pnl``, ``day_pnl`` excludes the day's transfers, and returns are chained
time-weighted so that neither the size nor the timing of a transfer moves them.

Time-weighted return convention
-------------------------------
Equity is observed once per trading day (the daily snapshot; today's live value is the
last point). With ``E_t`` the equity at the end of day ``t`` and ``F_t`` the net transfer
(deposits - withdrawals) attributed to day ``t``, the day's factor is::

    (E_t - F_t) / E_{t-1}          # E_{-1} = initial cash at the start of the epoch

and the return through day ``t`` is the product of the factors minus one. ``F_t`` collects
every transfer dated in ``(previous trading day, t]`` (Taipei calendar), so a weekend
deposit belongs to Monday. A day's flow is taken out of the day's closing equity and
measured against the previous close: the flow is neither profit nor loss and earns nothing
on the day it arrives. (A deposit that doubles the account on a flat day gives exactly
0% for the day, a withdrawal on a flat day gives 0%, and ``day_pnl = E_t - E_{t-1} - F_t``
is the numerator of the same day figure.) A factor is floored at 0 (a total loss) and a
day that starts from zero equity counts as 1. Arithmetic is ``Decimal`` until the final
rounding to two places.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import UUID

from quant_signal.domain.paper import (
    MAX_CASH,
    MAX_TRANSFERS_PER_DAY,
    TRANSFER_KINDS,
    AccountSummary,
    LedgerEntry,
    Transfer,
    TransferList,
    TransferRequest,
)
from quant_signal.quant.paper_calendar import TAIPEI, local, local_date

if TYPE_CHECKING:
    from quant_signal.application.paper_trading import PaperService

log = logging.getLogger(__name__)
D = Decimal

TransferStatus = Literal[
    "ok", "replayed", "missing", "stale", "daily_cap", "over_available", "ceiling"
]


@dataclass(frozen=True)
class TransferOutcome:
    status: TransferStatus
    entry: LedgerEntry | None = None  # the posting (the original one when ``replayed``)
    cash: int = 0
    available: int = 0


class TransferRepository(Protocol):
    async def apply_paper_transfer(
        self,
        entry: LedgerEntry,
        *,
        day_start: datetime,
        day_end: datetime,
        max_per_day: int,
        max_cash: int,
    ) -> TransferOutcome:
        """Post ``entry`` (signed amount) under the account lock, in one transaction.

        Order of checks: account gone/archived (``missing``) -> the same ``request_id``
        already posted (``replayed`` with the original entry, whatever epoch it was in) ->
        epoch changed since the caller read it (``stale``) -> more than ``max_per_day``
        transfers dated in ``[day_start, day_end)`` (``daily_cap``) -> a withdrawal above
        the available cash, i.e. cash minus the reserves of working buys (``over_available``)
        -> a deposit taking cash above ``max_cash`` (``ceiling``)."""
        ...

    async def list_paper_transfer_entries(
        self, account_id: UUID, *, epoch: int | None
    ) -> list[LedgerEntry]:
        """Transfer postings, oldest first; ``epoch=None`` returns every epoch."""
        ...


# --- pure helpers ----------------------------------------------------------------------


def is_transfer(entry: LedgerEntry) -> bool:
    return entry.kind in TRANSFER_KINDS


def transfer_flow(
    entries: Iterable[LedgerEntry], *, after: date | None = None, through: date | None = None
) -> int:
    """Net transfers (deposits - withdrawals) dated in ``(after, through]`` (Taipei dates)."""
    total = 0
    for entry in entries:
        if not is_transfer(entry):
            continue
        day = local_date(entry.at)
        if (after is None or day > after) and (through is None or day <= through):
            total += entry.amount
    return total


def contributed_capital(initial_cash: int, entries: Iterable[LedgerEntry]) -> int:
    return initial_cash + transfer_flow(entries)


def twr_factors(base_equity: int, steps: Sequence[tuple[int, int]]) -> list[Decimal]:
    """Daily factors ``(E_t - F_t) / E_{t-1}`` for ``steps = [(E_t, F_t), ...]``."""
    factors: list[Decimal] = []
    previous = base_equity
    for equity, flow in steps:
        if previous <= 0:
            factors.append(D(1))
        else:
            factors.append(max(D(0), D(equity - flow) / D(previous)))
        previous = equity
    return factors


def twr_return_pct(base_equity: int, steps: Sequence[tuple[int, int]]) -> float:
    """Chained time-weighted return in percent, rounded to two places."""
    total = D(1)
    for factor in twr_factors(base_equity, steps):
        total *= factor
    return float(((total - 1) * 100).quantize(D("0.01"), rounding=ROUND_HALF_UP))


def twr_index(points: Sequence[tuple[int, int]]) -> list[float]:
    """Index series (100 at the first point) for ``points = [(E_t, F_t), ...]``.

    The first point is the base, so its own flow does not enter; every later point chains
    ``(E_t - F_t) / E_{t-1}``."""
    if not points:
        return []
    index = D(100)
    out = [100.0]
    for factor in twr_factors(points[0][0], points[1:]):
        index *= factor
        out.append(round(float(index), 4))
    return out


def taipei_day_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(local(now).date(), time.min, TAIPEI)
    return start, start + timedelta(days=1)


# --- the service operations --------------------------------------------------------------


def _problem(outcome: TransferOutcome, direction: str, amount: int) -> str:
    if outcome.status == "daily_cap":
        return f"每個帳戶每天最多 {MAX_TRANSFERS_PER_DAY} 筆入金／出金，今天已達上限，請明天再試"
    if outcome.status == "over_available":
        return (
            f"出金 NT${amount:,} 超過可用資金 NT${max(outcome.available, 0):,}"
            "（現金扣掉未成交買單預留的資金）"
        )
    if outcome.status == "ceiling":
        return (
            f"入金後現金會超過 NT${MAX_CASH:,} 的上限（目前現金 NT${outcome.cash:,}），"
            "請降低入金金額"
        )
    if outcome.status == "stale":
        return "帳戶剛被重設，請重新整理後再試"
    return "找不到這個模擬帳戶"


async def make_transfer(
    svc: PaperService, user_id: UUID, account_id: UUID, body: TransferRequest
) -> AccountSummary:
    """Deposit or withdraw simulated cash; idempotent on ``body.request_id``."""
    from quant_signal.application.paper_trading import PaperConflict, PaperNotFound

    account = await svc._own(user_id, account_id)
    now = svc.clock()
    deposit = body.direction == "in"
    entry = LedgerEntry(
        account_id=account.id,
        epoch=account.epoch,
        at=now,
        kind="transfer_in" if deposit else "transfer_out",
        amount=body.amount if deposit else -body.amount,
        request_id=body.request_id,
        note=body.note,
    )
    start, end = taipei_day_bounds(now)
    outcome = await svc.repo.apply_paper_transfer(  # type: ignore[attr-defined]
        entry,
        day_start=start,
        day_end=end,
        max_per_day=MAX_TRANSFERS_PER_DAY,
        max_cash=MAX_CASH,
    )
    if outcome.status == "missing":
        raise PaperNotFound("找不到這個模擬帳戶")
    if outcome.status not in ("ok", "replayed"):
        raise PaperConflict(_problem(outcome, body.direction, body.amount))
    if outcome.status == "ok":
        await _refresh_todays_snapshot(svc, account.id, now)
    return (await svc._summaries([await svc._own(user_id, account_id)]))[0]


async def _refresh_todays_snapshot(svc: PaperService, account_id: UUID, now: datetime) -> None:
    """Re-write today's snapshot when it was already written (a transfer after 14:45).

    The snapshot of a day holds the cash through that day and its ``net_transfer``; the
    job does not revisit a final day, so a 20:00 deposit would otherwise be missing from
    that day's row. Before 14:45 (or on a non-trading day) the transfer belongs to a later
    snapshot and nothing is written. A failure here is logged, never raised: the transfer
    itself is booked."""
    from quant_signal.application.paper_jobs import PaperJobs

    try:
        day = PaperJobs._snapshot_day(now)
        if local(now).date() > day:
            return
        account = await svc.repo.get_paper_account(account_id)
        if account is None or account.archived:
            return
        known = await svc.repo.list_paper_snapshots(account.id, epoch=account.epoch, since=day)
        if not any(s.trading_date == day for s in known):
            return  # not written yet: the job will include the transfer
        snapshot, _ = await PaperJobs(slippage_ticks=svc.slippage_ticks).snapshot_account(
            svc.repo, account, day, now
        )
        await svc.repo.upsert_paper_snapshot(snapshot)
    except Exception:
        log.warning("Could not refresh today's paper snapshot after a transfer", exc_info=True)


async def list_transfers(
    svc: PaperService,
    user_id: UUID,
    account_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
    include_archived: bool = False,
) -> TransferList:
    account = await svc._own(user_id, account_id)
    entries = await svc.repo.list_paper_transfer_entries(  # type: ignore[attr-defined]
        account.id, epoch=None if include_archived else account.epoch
    )
    newest_first = sorted(entries, key=lambda e: (e.at, str(e.id)), reverse=True)
    page = newest_first[offset : offset + limit]
    return TransferList(
        transfers=[
            Transfer(
                id=e.id,
                direction="in" if e.kind == "transfer_in" else "out",
                amount=abs(e.amount),
                at=e.at.astimezone(UTC),
                note=e.note,
            )
            for e in page
        ],
        has_more=offset + len(page) < len(newest_first),
    )
