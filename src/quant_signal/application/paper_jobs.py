"""Background jobs for paper trading: matching, reconciliation, snapshots, demand.

``PaperJobs.tick`` is called every few seconds by the ingest service's ``paper-matcher``
task. Every write goes through ``PaperRepository`` methods that lock the order row and
re-check ``working``, so a second worker, a retry or a cancel racing the matcher can
never book an order twice.

Prices are read only from our own tables (``quote_latest``, ``minute_bars``,
``market_stock_daily``); there is no broker call and no Shioaji import here.
"""

from __future__ import annotations

import asyncio
import logging
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from time import perf_counter
from typing import Protocol
from uuid import UUID

from quant_signal.application.paper_market import (
    MarketView,
    account_start_date,
    benchmark_closes,
    load_market,
    official_closes_on,
    past_references,
)
from quant_signal.application.paper_ports import PaperInsufficientCash, PaperRepository
from quant_signal.domain.paper import (
    LedgerEntry,
    PaperAccountRecord,
    PaperFillRecord,
    PaperOrderRecord,
    SnapshotRecord,
)
from quant_signal.domain.tw_trading import BOARD_LOT, price_limit_pct_for
from quant_signal.infrastructure.db.prices import MinuteBar
from quant_signal.quant.paper_book import replay
from quant_signal.quant.paper_calendar import (
    is_trading_day,
    local,
    matcher_open,
    observation_window,
    previous_trading_day,
)
from quant_signal.quant.paper_matching import (
    Match,
    Observation,
    OrderSpec,
    compute_fill,
    day_trade_shares,
    limits_for_symbol,
    match_order,
    resolve_reference,
    symbol_is_etf,
    to_money,
    to_price,
)

log = logging.getLogger(__name__)
D = Decimal

DEMAND_RENEW_SECONDS = 20.0
SNAPSHOT_RETRY_SECONDS = 900.0
RECONCILE_EVERY_SECONDS = 60.0
RECONCILE_AFTER = time(14, 40)  # official minute bars land after ~14:35
FALLBACK_EXPIRE_AT = time(16, 0)  # expire with whatever was observed
SNAPSHOT_AFTER = time(14, 45)
DEMAND_OPENS = time(8, 50)
DEMAND_CLOSES = time(13, 35)
# Paper trading may hold at most this many of the 200 collector subscriptions.
PAPER_DEMAND_MAX = 60
OFFICIAL_LAST_MINUTE = time(13, 20)  # an official bar this late means the day is complete
# Every loop over orders/accounts yields to the event loop at least this often: the matcher
# shares its loop with the realtime quote collector and must never starve it.
YIELD_EVERY = 50
YIELD_AFTER_SECONDS = 0.004


class _Breather:
    """``await breathe()`` once per item: yields every ``YIELD_EVERY`` items, and sooner
    when the loop has been busy for more than ``YIELD_AFTER_SECONDS`` (a heavy order)."""

    def __init__(self) -> None:
        self.count = 0
        self.last = perf_counter()

    async def __call__(self) -> None:
        self.count += 1
        if self.count % YIELD_EVERY == 0 or perf_counter() - self.last > YIELD_AFTER_SECONDS:
            await asyncio.sleep(0)
            self.last = perf_counter()


class DemandPort(Protocol):
    async def admit(self, symbol: str) -> bool:
        """Ask for ``symbol`` to be subscribed; False when the shared cap is full."""
        ...


@dataclass
class TickStats:
    matched: int = 0
    expired: int = 0
    rejected: int = 0
    equity_rows: int = 0
    demand: list[str] = field(default_factory=list)


@dataclass
class _SnapshotState:
    checked_at: datetime
    written: set[tuple[UUID, int]] = field(default_factory=set)  # (account, epoch) done
    final: bool = False


class PaperJobs:
    def __init__(
        self,
        *,
        slippage_ticks: int = 1,
        demand: DemandPort | None = None,
        demand_max: int = PAPER_DEMAND_MAX,
    ) -> None:
        self.slippage_ticks = slippage_ticks
        self.demand = demand
        self.demand_max = demand_max
        self._last_bar_minute: datetime | None = None
        self._last_demand: datetime | None = None
        self._last_reconcile: datetime | None = None
        self._snapshot_state: dict[date, _SnapshotState] = {}
        # Per symbol: the minute of the newest bar already matched, and a fingerprint of the
        # prices the working orders were last checked against. Orders checked against an
        # unchanged fingerprint are not looked at again.
        self._bar_watermark: dict[str, datetime] = {}
        self._fingerprint: dict[str, tuple] = {}
        self._examined: set[UUID] = set()

    # --- entry point ---------------------------------------------------------------

    async def tick(self, repo: PaperRepository, now: datetime) -> TickStats:
        stats = TickStats()
        local_now = local(now)
        working = await repo.list_working_paper_orders()
        if self.demand is not None and self._demand_window(local_now):
            if (
                self._last_demand is None
                or (now - self._last_demand).total_seconds() >= DEMAND_RENEW_SECONDS
            ):
                stats.demand = await self.register_demand(repo, working, now)
                self._last_demand = now
        if working and matcher_open(now):
            await self._match_live(repo, working, now, stats)
        if working and (
            self._last_reconcile is None
            or (now - self._last_reconcile).total_seconds() >= RECONCILE_EVERY_SECONDS
        ):
            self._last_reconcile = now
            await self.reconcile(repo, now, stats)
        if stats.matched or stats.expired or stats.rejected:
            working = await repo.list_working_paper_orders()  # what is left after this tick
        await self.write_daily_snapshots(repo, now, stats, working=working)
        return stats

    @staticmethod
    def _demand_window(local_now: datetime) -> bool:
        return (
            is_trading_day(local_now.date()) and DEMAND_OPENS <= local_now.time() <= DEMAND_CLOSES
        )

    # --- demand --------------------------------------------------------------------

    async def register_demand(
        self, repo: PaperRepository, working: list[PaperOrderRecord], now: datetime
    ) -> list[str]:
        """Ask the shared, capped demand set for the symbols paper trading cares about.

        Working orders for today first, then open positions (one aggregate query, not a
        replay of every account's fills). Each symbol is requested on its own through the
        same all-or-nothing ``ADMIT`` script real viewers use, so a full set simply refuses
        it (those symbols stay on 10-minute snapshots and minute bars and the API marks
        them ``price_delayed``). Paper trading never asks for more than ``demand_max``
        symbols.
        """
        assert self.demand is not None
        today = local(now).date()
        wanted: list[str] = []
        seen: set[str] = set()
        breathe = _Breather()
        for order in working:
            if order.session_date == today and order.symbol not in seen:
                seen.add(order.symbol)
                wanted.append(order.symbol)
            await breathe()
        for symbol in await repo.list_open_position_symbols():
            if symbol not in seen:
                seen.add(symbol)
                wanted.append(symbol)
        admitted: list[str] = []
        for symbol in wanted:
            if len(admitted) >= self.demand_max:
                break
            try:
                if await self.demand.admit(symbol):
                    admitted.append(symbol)
            except Exception:
                log.warning("Paper demand registration failed; will retry")
                break
            await breathe()
        return admitted

    # --- live matching -------------------------------------------------------------

    async def _match_live(
        self,
        repo: PaperRepository,
        working: list[PaperOrderRecord],
        now: datetime,
        stats: TickStats,
    ) -> None:
        today = local(now).date()
        by_symbol: dict[str, list[PaperOrderRecord]] = {}
        for order in working:
            if order.session_date == today:
                by_symbol.setdefault(order.symbol, []).append(order)
        if not by_symbol:
            return
        self._examined &= {o.id for orders in by_symbol.values() for o in orders}
        symbols = sorted(by_symbol)
        views = await load_market(repo, symbols)
        minute = local(now).replace(second=0, microsecond=0)
        bars_due = self._last_bar_minute != minute
        self._last_bar_minute = minute
        first, _ = observation_window(today)
        accounts: dict[UUID, PaperAccountRecord | None] = {}
        breathe = _Breather()
        for symbol in symbols:
            view = views.get(symbol)
            if view is None or not view.known:
                continue
            mark = self._bar_watermark.get(symbol)
            new_bars: list[MinuteBar] = []
            if bars_due:
                since = first if mark is None or mark < first else mark + timedelta(minutes=1)
                new_bars = await repo.list_minute_bars(symbol, since=since, until=minute)
            quote = view.quote
            fingerprint = (
                None if quote is None else (quote.trade_time, quote.price, quote.reference),
                None if quote is None else quote.open,
                view.official_date,
                view.official_close,
            )
            symbol_orders = by_symbol[symbol]
            if new_bars or self._fingerprint.get(symbol) != fingerprint:
                todo = symbol_orders
            else:  # same prices, no new bars: only orders not yet looked at
                todo = [o for o in symbol_orders if o.id not in self._examined]
            if todo:
                shared = self.point_observations(view) + [self.bar_observation(b) for b in new_bars]
                shared.sort(key=lambda o: o.time)
                await breathe()
                times = [o.time for o in shared]
                for order in todo:
                    found = shared[bisect_right(times, order.created_at) :]
                    opening = self.open_observation(view, order)
                    if opening is not None:
                        found = [opening, *found]
                    if found:
                        await self.settle(repo, order, view, found, now, stats, accounts)
                    await breathe()
            self._examined.update(o.id for o in symbol_orders)
            self._fingerprint[symbol] = fingerprint
            if new_bars:
                self._bar_watermark[symbol] = max(mark or new_bars[0].minute, new_bars[-1].minute)

    # --- observations --------------------------------------------------------------

    @staticmethod
    def point_observations(view: MarketView) -> list[Observation]:
        quote = view.quote
        if quote is None:
            return []
        return [
            Observation(
                time=quote.trade_time,
                source="tick" if quote.source == "tick" else "snapshot",
                price=to_price(quote.price),
            )
        ]

    @staticmethod
    def open_observation(view: MarketView, order: PaperOrderRecord) -> Observation | None:
        """An order waiting for the open meets the day's first trade, the open price."""
        quote = view.quote
        first, _ = observation_window(order.session_date)
        if (
            quote is not None
            and order.created_at < first
            and quote.open
            and view.quote_date == order.session_date
        ):
            return Observation(time=first, source="open", price=to_price(quote.open))
        return None

    @classmethod
    def quote_observations(cls, view: MarketView, order: PaperOrderRecord) -> list[Observation]:
        found = cls.point_observations(view)
        opening = cls.open_observation(view, order)
        return found if opening is None else [*found, opening]

    @staticmethod
    def bar_observation(bar: MinuteBar) -> Observation:
        return Observation(
            time=bar.minute,
            source="minute_bar",
            price=to_price(bar.open),
            high=to_price(bar.high),
            low=to_price(bar.low),
        )

    # --- one order ----------------------------------------------------------------

    async def settle(
        self,
        repo: PaperRepository,
        order: PaperOrderRecord,
        view: MarketView,
        observations: list[Observation],
        now: datetime,
        stats: TickStats,
        accounts: dict[UUID, PaperAccountRecord | None],
        *,
        reference: Decimal | None = None,
    ) -> bool:
        """Match ``order`` against ``observations`` and book the fill. True if filled.

        ``reference`` is the session's reference price when the caller already knows it
        (a finished session reconciled later); otherwise it is derived from the market view.
        """
        etf = symbol_is_etf(order.symbol)
        quote = view.quote
        if reference is None:
            reference = resolve_reference(
                order.session_date,
                quote_reference=(D(str(quote.reference)) if quote and quote.reference else None),
                quote_price=(D(str(quote.price)) if quote else None),
                quote_date=view.quote_date,
                official_close=view.official_close,
                official_date=view.official_date,
            )
        limit_down: Decimal | None = None
        limit_up: Decimal | None = None
        if price_limit_pct_for(order.symbol) is not None:  # symbols without a limit need none
            if reference is None:
                return False
            limit_down, limit_up = limits_for_symbol(reference, order.symbol)
        spec = OrderSpec(
            side=order.side,
            order_type=order.order_type,
            limit_price=order.limit_price,
            created_at=order.created_at,
            session_date=order.session_date,
            etf=etf,
        )
        found = match_order(
            spec,
            observations,
            limit_down=limit_down,
            limit_up=limit_up,
            slippage_ticks=self.slippage_ticks,
        )
        if found is None:
            return False
        if order.account_id not in accounts:
            accounts[order.account_id] = await repo.get_paper_account(order.account_id)
        account = accounts[order.account_id]
        if account is None or account.archived or account.epoch != order.epoch:
            await repo.finish_paper_order(order.id, status="cancelled", now=now)
            return False
        return await self.book(repo, account, order, found, now, stats)

    async def book(
        self,
        repo: PaperRepository,
        account: PaperAccountRecord,
        order: PaperOrderRecord,
        found: Match,
        now: datetime,
        stats: TickStats,
    ) -> bool:
        shares = order.lots * BOARD_LOT
        day = order.session_date
        dt_qty = 0
        if order.side == "sell":
            book = replay(await repo.list_paper_fills(account.id, epoch=account.epoch))
            if book.held(order.symbol) < shares:
                if await repo.finish_paper_order(
                    order.id, status="rejected", now=now, reject_reason="成交時持股不足，委託作廢"
                ):
                    stats.rejected += 1
                return False
            if account.allow_day_trade:
                dt_qty = day_trade_shares(
                    shares,
                    bought_today=book.bought_on(order.symbol, day),
                    sold_today=book.sold_on(order.symbol, day),
                )
        amounts = compute_fill(
            order.side,
            shares,
            found.price,
            symbol=order.symbol,
            commission_discount=account.commission_discount,
            day_trade_qty=dt_qty,
            trade_date=local(found.time).date(),
        )
        fill = PaperFillRecord(
            order_id=order.id,
            account_id=account.id,
            epoch=account.epoch,
            symbol=order.symbol,
            side=order.side,
            shares=shares,
            price=found.price,
            amount=amounts.amount,
            commission=amounts.commission,
            tax=amounts.tax,
            day_trade=amounts.day_trade,
            filled_at=found.time,
            price_source=found.source,
            price_time=found.time,
        )
        ledger = ledger_entries(fill, now)
        try:
            # The repository re-checks, under the account lock, that the cash left after
            # the reserves of the account's other working orders covers this fill.
            if await repo.apply_paper_fill(fill, ledger, now=now):
                stats.matched += 1
                return True
        except PaperInsufficientCash:
            if await repo.finish_paper_order(
                order.id, status="rejected", now=now, reject_reason="成交時資金不足，委託作廢"
            ):
                stats.rejected += 1
        return False

    # --- after the close -----------------------------------------------------------

    async def reconcile(
        self, repo: PaperRepository, now: datetime, stats: TickStats | None = None
    ) -> TickStats:
        """Replay each finished session's minute bars against its still-working orders.

        Catches touches the 3-second poll missed, then expires what never traded. Runs
        once official minutes are stored (after ~14:40), or at 16:00 with whatever was
        observed. Idempotent: booking and expiry both require ``working``. Every date
        whose fills or expiries changed has its equity snapshots rewritten afterwards.
        """
        stats = stats or TickStats()
        local_now = local(now)
        today = local_now.date()
        working = await repo.list_working_paper_orders()
        due_days = sorted({o.session_date for o in working if o.session_date <= today})
        accounts: dict[UUID, PaperAccountRecord | None] = {}
        touched: dict[date, set[UUID]] = {}
        breathe = _Breather()
        for day in due_days:
            if day == today and local_now.time() < RECONCILE_AFTER:
                continue
            orders = [o for o in working if o.session_date == day]
            symbols = sorted({o.symbol for o in orders})
            views = await load_market(repo, symbols)
            references = await past_references(repo, symbols, day) if day < today else {}
            first, last = observation_window(day)
            bar_obs: dict[str, list[Observation]] = {}
            bar_times: dict[str, list[datetime]] = {}
            complete: dict[str, bool] = {}
            for symbol in symbols:
                bars = await repo.list_minute_bars(
                    symbol, since=first, until=last + timedelta(minutes=1)
                )
                complete[symbol] = any(
                    b.official and local(b.minute).time() >= OFFICIAL_LAST_MINUTE for b in bars
                )
                bar_obs[symbol] = sorted(
                    (self.bar_observation(b) for b in bars), key=lambda o: o.time
                )
                bar_times[symbol] = [o.time for o in bar_obs[symbol]]
                await breathe()
            final = day < today or local_now.time() >= FALLBACK_EXPIRE_AT
            for order in orders:
                await breathe()
                if not (complete[order.symbol] or final):
                    continue
                view = views.get(order.symbol)
                filled = False
                if view is not None and view.known:
                    mine = bar_obs[order.symbol]
                    observations = mine[bisect_right(bar_times[order.symbol], order.created_at) :]
                    observations = observations + self.quote_observations(view, order)
                    if not mine and view.official_date == day and view.official_open:
                        if order.created_at < first:
                            observations.append(
                                Observation(
                                    time=first,
                                    source="open",
                                    price=to_price(view.official_open),
                                )
                            )
                    filled = await self.settle(
                        repo,
                        order,
                        view,
                        observations,
                        now,
                        stats,
                        accounts,
                        reference=references.get(order.symbol),
                    )
                if filled or await repo.finish_paper_order(order.id, status="expired", now=now):
                    touched.setdefault(day, set()).add(order.account_id)
                    if not filled:
                        stats.expired += 1
        if touched:
            await self._rewrite_snapshots(repo, touched, now, stats)
        return stats

    # --- daily snapshots -----------------------------------------------------------

    @staticmethod
    def _snapshot_day(now: datetime) -> date:
        local_now = local(now)
        today = local_now.date()
        if is_trading_day(today) and local_now.time() >= SNAPSHOT_AFTER:
            return today
        return previous_trading_day(today)

    async def _rewrite_snapshots(
        self,
        repo: PaperRepository,
        touched: dict[date, set[UUID]],
        now: datetime,
        stats: TickStats,
    ) -> None:
        """Re-value the days whose fills changed (and the later days already written)."""
        newest = self._snapshot_day(now)
        breathe = _Breather()
        for day, account_ids in sorted(touched.items()):
            for account_id in sorted(account_ids, key=str):
                account = await repo.get_paper_account(account_id)
                if account is None or account.archived:
                    continue
                known = await repo.list_paper_snapshots(account.id, epoch=account.epoch, since=day)
                days = {day} | {s.trading_date for s in known}
                for target in sorted(d for d in days if d <= newest):
                    if account_start_date(account) > target:
                        continue
                    snapshot, _ = await self.snapshot_account(repo, account, target, now)
                    await repo.upsert_paper_snapshot(snapshot)
                    stats.equity_rows += 1
                await breathe()

    async def write_daily_snapshots(
        self,
        repo: PaperRepository,
        now: datetime,
        stats: TickStats | None = None,
        *,
        working: list[PaperOrderRecord] | None = None,
    ) -> TickStats:
        """Upsert one equity snapshot per account for the latest finished trading day.

        Runs from 14:45, retried every 15 minutes. A day is only *final* once every
        position is valued at the official close AND no order that could still back-date a
        fill into it is working (reconciliation for that session has completed); until then
        the row is provisional and is rewritten. Once final, accounts that appear later (a
        reset starts a new epoch) still get their row at the next check. Safe to repeat:
        it is an upsert.
        """
        stats = stats or TickStats()
        day = self._snapshot_day(now)
        state = self._snapshot_state.get(day)
        if state is not None and (now - state.checked_at).total_seconds() < SNAPSHOT_RETRY_SECONDS:
            return stats
        if working is None:
            working = await repo.list_working_paper_orders()
        pending = any(o.session_date <= day for o in working)
        previous = state or _SnapshotState(checked_at=now)
        fresh = _SnapshotState(
            checked_at=now, written=set(previous.written) if previous.final else set()
        )
        self._snapshot_state[day] = fresh
        all_official = True
        breathe = _Breather()
        for account in await repo.list_active_paper_accounts():
            if account_start_date(account) > day:
                continue
            if (account.id, account.epoch) in fresh.written:
                continue
            snapshot, official = await self.snapshot_account(repo, account, day, now)
            await repo.upsert_paper_snapshot(snapshot)
            fresh.written.add((account.id, account.epoch))
            stats.equity_rows += 1
            all_official = all_official and official
            await breathe()
        fresh.final = all_official and not pending
        return stats

    async def snapshot_account(
        self, repo: PaperRepository, account: PaperAccountRecord, day: date, now: datetime
    ) -> tuple[SnapshotRecord, bool]:
        """(snapshot, every position valued at the official close)."""
        fills = [
            f
            for f in await repo.list_paper_fills(account.id, epoch=account.epoch)
            if local(f.filled_at).date() <= day
        ]
        book = replay(fills)
        ledger = await repo.list_paper_ledger(account.id, account.epoch)
        cash = sum(e.amount for e in ledger if local(e.at).date() <= day)
        open_positions = book.open_positions()
        closes = await official_closes_on(repo, [p.symbol for p in open_positions], day)
        views = await load_market(repo, [p.symbol for p in open_positions])
        market_value = 0
        official = True
        for state in open_positions:
            price = closes.get(state.symbol)
            if price is None:
                official = False
                view = views.get(state.symbol)
                if view and view.quote and view.quote_date == day:
                    price = to_price(view.quote.price)
                elif (
                    view
                    and view.official_close
                    and (view.official_date is not None and view.official_date <= day)
                ):
                    price = view.official_close
                else:
                    price = state.avg_cost.quantize(D("0.01"))
            market_value += to_money(D(state.shares) * price)
        benchmark = (await benchmark_closes(repo, day, day)).get(day)
        return (
            SnapshotRecord(
                account_id=account.id,
                trading_date=day,
                epoch=account.epoch,
                cash=cash,
                market_value=market_value,
                equity=cash + market_value,
                realized_pnl_cum=book.realized_total,
                benchmark_close=benchmark,
            ),
            official,
        )


def ledger_entries(fill: PaperFillRecord, now: datetime) -> list[LedgerEntry]:
    """The cash postings of one fill: buy -amount -commission; sell +amount -commission -tax."""
    entries = [
        LedgerEntry(
            account_id=fill.account_id,
            epoch=fill.epoch,
            at=fill.filled_at,
            kind=fill.side,
            amount=-fill.amount if fill.side == "buy" else fill.amount,
            fill_id=fill.id,
        ),
        LedgerEntry(
            account_id=fill.account_id,
            epoch=fill.epoch,
            at=fill.filled_at,
            kind="commission",
            amount=-fill.commission,
            fill_id=fill.id,
        ),
    ]
    if fill.tax:
        entries.append(
            LedgerEntry(
                account_id=fill.account_id,
                epoch=fill.epoch,
                at=fill.filled_at,
                kind="tax",
                amount=-fill.tax,
                fill_id=fill.id,
            )
        )
    return entries
