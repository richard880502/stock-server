"""Dividends for paper accounts: the one place that builds a book, and the job that credits them.

``load_book`` is the ONLY way application code turns storage into a ``Book``: it loads the
fills and the corporate actions of an account epoch together, so positions, sellable
shares, realised P&L, snapshots and reconciliation can never disagree about a bonus share.
(``tests/test_paper_dividends.py`` fails if any other module calls ``replay``.)

Known limitations, deliberately not guessed around:

* An event that was already applied and is later withdrawn or revised is NOT unwound; the
  job logs a warning once per process (``audit``).
* An older event that only becomes visible (``available_at``) after a later event of the
  same symbol was applied is still applied correctly for itself, but cannot re-size the
  later one.
* Cash capital increases, capital reductions and splits that do not arrive as
  ``stock_dividend_per_share`` are skipped.
* Events carrying more than one non-withdrawn row for the same symbol and ex-date are
  ambiguous and skipped (logged).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from uuid import UUID

from quant_signal.application.paper_market import account_start_date, load_market
from quant_signal.domain.paper import PaperAccountRecord, PaperFillRecord
from quant_signal.domain.paper_actions import (
    CorporateAction,
    CorporateActionList,
    CorporateActionRecord,
)
from quant_signal.domain.short_data import DividendEvent
from quant_signal.quant.paper_book import Book, replay
from quant_signal.quant.paper_calendar import local, local_date
from quant_signal.quant.paper_dividends import entitled_shares, ex_at, payout

log = logging.getLogger(__name__)

APPLY_FROM = time(8, 0)  # an ex-date is applied from 08:00 Taipei on that day
MAX_APPLY_PER_RUN = 200  # applications per run; the rest waits for the next run
YIELD_EVERY = 20


class ActionsRepository(Protocol):
    async def list_paper_fills(
        self, account_id: UUID, *, epoch: int | None
    ) -> list[PaperFillRecord]: ...

    async def list_paper_corporate_actions(
        self, account_id: UUID, *, epoch: int | None
    ) -> list[CorporateActionRecord]: ...


# --- the single book loader --------------------------------------------------------


async def load_book(
    repo: ActionsRepository, account_id: UUID, epoch: int, *, through: date | None = None
) -> Book:
    """The account epoch's book from its fills and corporate actions.

    ``through`` limits it to what had happened by the end of that Taipei date (fills filled
    on or before it, actions with an ex-date on or before it): the book a snapshot values.
    """
    fills = await repo.list_paper_fills(account_id, epoch=epoch)
    actions = await repo.list_paper_corporate_actions(account_id, epoch=epoch)
    if through is not None:
        fills = [f for f in fills if local_date(f.filled_at) <= through]
        actions = [a for a in actions if a.ex_date <= through]
    return replay(fills, actions)


def realized_by_fill(
    fills: Iterable[PaperFillRecord], actions: Iterable[CorporateActionRecord]
) -> dict[UUID, int]:
    """Realised P&L per sell fill across every epoch, each epoch replayed on its own."""
    fills, actions = list(fills), list(actions)
    realized: dict[UUID, int] = {}
    for epoch in {f.epoch for f in fills}:
        realized.update(
            replay(
                [f for f in fills if f.epoch == epoch], [a for a in actions if a.epoch == epoch]
            ).realized_by_fill
        )
    return realized


# --- listing -----------------------------------------------------------------------


async def list_corporate_actions(
    repo, account: PaperAccountRecord, *, limit: int, offset: int, include_archived: bool
) -> CorporateActionList:
    found = await repo.list_paper_corporate_actions(
        account.id, epoch=None if include_archived else account.epoch
    )
    newest_first = sorted(found, key=lambda a: (a.ex_at, a.symbol, str(a.id)), reverse=True)
    page = newest_first[offset : offset + limit]
    views = await load_market(repo, sorted({a.symbol for a in page}))
    return CorporateActionList(
        actions=[
            CorporateAction(
                id=a.id,
                symbol=a.symbol,
                name=(
                    views[a.symbol].name if a.symbol in views and views[a.symbol].name else a.symbol
                ),
                ex_date=a.ex_date,
                kind=a.event_kind,
                entitled_shares=a.entitled_shares,
                cash_amount=a.cash_amount,
                shares_added=a.shares_added,
                cash_dividend_per_share=_f(a.cash_dividend_per_share),
                stock_dividend_per_share=_f(a.stock_dividend_per_share),
                applied_at=a.applied_at.astimezone(UTC),
            )
            for a in page
        ],
        has_more=offset + len(page) < len(newest_first),
    )


def _f(value) -> float | None:
    return None if value is None else float(value)


# --- the job -----------------------------------------------------------------------


@dataclass
class ApplyReport:
    applied: list[tuple[UUID, date]] = field(default_factory=list)  # (account, ex_date)
    complete: bool = True  # False when the per-run budget ran out


def cutoff_date(now: datetime) -> date:
    """Newest ex-date that may be applied: today from 08:00 Taipei, else yesterday."""
    moment = local(now)
    return moment.date() if moment.time() >= APPLY_FROM else moment.date() - timedelta(days=1)


def _event_key(event: DividendEvent) -> tuple[str, date]:
    return event.symbol, event.ex_date


async def apply_due_corporate_actions(
    repo, now: datetime, *, audit: set | None = None, budget: int = MAX_APPLY_PER_RUN
) -> ApplyReport:
    """Credit every dividend event that is due and not yet booked, per account, oldest first.

    Idempotent (the storage's unique key decides, never this function) and bounded by
    ``budget``. Safe to call from any number of workers or after a restart: all state it
    needs is in the database. Only events with ``available_at <= now`` are used.
    """
    report = ApplyReport()
    audit = audit if audit is not None else set()
    cutoff = cutoff_date(now)
    pairs = await repo.list_paper_traded_pairs()
    if not pairs:
        return report
    traded: dict[UUID, set[str]] = {}
    for account_id, symbol in pairs:
        traded.setdefault(account_id, set()).add(symbol)
    accounts = {a.id: a for a in await repo.list_active_paper_accounts() if a.id in traded}
    if not accounts:
        return report
    start = min(account_start_date(a) for a in accounts.values())
    symbols = sorted({s for account_id in accounts for s in traded[account_id]})
    events = await repo.list_dividend_events(symbols, start, cutoff, now)
    by_symbol_day: dict[tuple[str, date], list[DividendEvent]] = {}
    for event in events:
        by_symbol_day.setdefault(_event_key(event), []).append(event)
    chosen: list[DividendEvent] = []
    for key, group in sorted(by_symbol_day.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if len(group) > 1:
            _warn_once(
                audit,
                ("ambiguous", *key),
                "paper dividends: %d events for %s on %s; skipped",
                len(group),
                *key,
            )
            continue
        chosen.append(group[0])
    if not chosen:
        return report

    for index, account in enumerate(sorted(accounts.values(), key=lambda a: str(a.id))):
        if index % YIELD_EVERY == 0:
            await asyncio.sleep(0)
        mine = [
            e
            for e in chosen
            if e.symbol in traded[account.id] and e.ex_date > account_start_date(account)
        ]
        done = await repo.list_paper_corporate_actions(account.id, epoch=account.epoch)
        applied_keys = {(a.symbol, a.ex_date): a for a in done}
        _audit_applied(audit, chosen, applied_keys)
        if not mine:
            continue
        pending = [e for e in mine if _event_key(e) not in applied_keys]
        if not pending:
            continue
        # An order of an earlier session that is still working could be reconciled later
        # into a back-dated fill; wait until it is settled so the entitlement cannot move.
        working = await repo.list_working_paper_orders(account.id)
        fills = await repo.list_paper_fills(account.id, epoch=account.epoch)
        for event in pending:
            if any(o.session_date < event.ex_date and o.epoch == account.epoch for o in working):
                continue
            if len(report.applied) >= budget:
                report.complete = False
                return report
            held = entitled_shares(fills, done, event.symbol, event.ex_date)
            paid = payout(held, event.cash_dividend_per_share, event.stock_dividend_per_share)
            if paid is None:
                continue
            record = CorporateActionRecord(
                account_id=account.id,
                epoch=account.epoch,
                symbol=event.symbol,
                ex_date=event.ex_date,
                event_kind=event.kind,
                entitled_shares=paid.entitled,
                cash_dividend_per_share=paid.cash_per_share,
                stock_dividend_per_share=paid.stock_per_share,
                cash_amount=paid.cash_amount,
                shares_added=paid.shares_added,
                ex_at=ex_at(event.ex_date),
                applied_at=now,
                source=event.source,
                source_revision=event.revision,
                source_available_at=event.available_at,
            )
            if await repo.apply_paper_corporate_action(record):
                report.applied.append((account.id, event.ex_date))
            done = await repo.list_paper_corporate_actions(account.id, epoch=account.epoch)
    return report


def _warn_once(audit: set, key: tuple, message: str, *args) -> None:
    if key not in audit:
        audit.add(key)
        log.warning(message, *args)


def _audit_applied(
    audit: set,
    current: list[DividendEvent],
    applied: dict[tuple[str, date], CorporateActionRecord],
) -> None:
    """Warn (once) when an already-applied event now reads differently. Never unwinds."""
    live = {_event_key(e): e for e in current}
    for key, action in applied.items():
        event = live.get(key)
        if event is None:
            _warn_once(
                audit,
                ("gone", action.id),
                "paper dividends: applied event %s %s is no longer listed (withdrawn?); "
                "booked amounts are left as they are",
                *key,
            )
        elif event.revision != action.source_revision and (
            _cash(event) != _cash(action) or _stock(event) != _stock(action)
        ):
            _warn_once(
                audit,
                ("revised", action.id, event.revision),
                "paper dividends: applied event %s %s was revised to different amounts; "
                "booked amounts are left as they are",
                *key,
            )


def _cash(item) -> float:
    value = getattr(item, "cash_dividend_per_share", None)
    return float(value or 0)


def _stock(item) -> float:
    value = getattr(item, "stock_dividend_per_share", None)
    return float(value or 0)
