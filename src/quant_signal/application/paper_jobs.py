"""Background jobs for paper trading: matching, reconciliation, snapshots, demand.

``PaperJobs.tick`` is called every few seconds by the ingest service's ``paper-matcher``
task. Every write goes through ``PaperRepository`` methods that lock the order row and
re-check ``working``, so a second worker, a retry or a cancel racing the matcher can
never book an order twice.

Prices are read only from our own tables (``quote_latest``, ``minute_bars``,
``market_stock_daily``); there is no broker call and no Shioaji import here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from quant_signal.application.paper_market import (
    MarketView,
    account_start_date,
    benchmark_closes,
    load_market,
)
from quant_signal.application.paper_ports import PaperRepository
from quant_signal.domain.paper import (
    LedgerEntry,
    PaperAccountRecord,
    PaperFillRecord,
    PaperOrderRecord,
    SnapshotRecord,
)
from quant_signal.domain.tw_trading import BOARD_LOT
from quant_signal.infrastructure.db.prices import MinuteBar
from quant_signal.quant.paper_book import replay
from quant_signal.quant.paper_calendar import (
    in_session,
    is_trading_day,
    local,
    previous_trading_day,
    session_window,
)
from quant_signal.quant.paper_matching import (
    Match,
    Observation,
    OrderSpec,
    compute_fill,
    day_trade_shares,
    limits_for,
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
        self._snapshot_state: dict[date, datetime | str] = {}

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
        if working and in_session(now):
            await self._match_live(repo, working, now, stats)
        if working and (
            self._last_reconcile is None
            or (now - self._last_reconcile).total_seconds() >= RECONCILE_EVERY_SECONDS
        ):
            self._last_reconcile = now
            await self.reconcile(repo, now, stats)
        await self.write_daily_snapshots(repo, now, stats)
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

        Working orders for today first, then open positions. Each symbol is requested on
        its own through the same all-or-nothing ``ADMIT`` script real viewers use, so a
        full set simply refuses it (those symbols stay on 10-minute snapshots and minute
        bars and the API marks them ``price_delayed``). Paper trading never asks for more
        than ``demand_max`` symbols.
        """
        assert self.demand is not None
        today = local(now).date()
        wanted: list[str] = []
        for order in working:
            if order.session_date == today and order.symbol not in wanted:
                wanted.append(order.symbol)
        for account in await repo.list_active_paper_accounts():
            fills = await repo.list_paper_fills(account.id, epoch=account.epoch)
            for state in replay(fills).open_positions():
                if state.symbol not in wanted:
                    wanted.append(state.symbol)
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
        orders = [o for o in working if o.session_date == today]
        if not orders:
            return
        symbols = sorted({o.symbol for o in orders})
        views = await load_market(repo, symbols)
        minute = local(now).replace(second=0, microsecond=0)
        bars_due = self._last_bar_minute != minute
        self._last_bar_minute = minute
        bars: dict[str, list[MinuteBar]] = {}
        if bars_due:
            first, _ = session_window(today)
            for symbol in symbols:
                bars[symbol] = [
                    bar for bar in await repo.list_minute_bars(symbol, since=first, until=minute)
                ]
        accounts: dict[UUID, PaperAccountRecord | None] = {}
        for order in orders:
            view = views.get(order.symbol)
            if view is None or not view.known:
                continue
            observations = self.quote_observations(view, order) + [
                self.bar_observation(b) for b in bars.get(order.symbol, [])
            ]
            if observations:
                await self.settle(repo, order, view, observations, now, stats, accounts)

    # --- observations --------------------------------------------------------------

    @staticmethod
    def quote_observations(view: MarketView, order: PaperOrderRecord) -> list[Observation]:
        quote = view.quote
        if quote is None:
            return []
        found = [
            Observation(
                time=quote.trade_time,
                source="tick" if quote.source == "tick" else "snapshot",
                price=to_price(quote.price),
            )
        ]
        first, _ = session_window(order.session_date)
        # An order waiting for the open meets the day's first trade, the open price.
        if order.created_at < first and quote.open and view.quote_date == order.session_date:
            found.append(Observation(time=first, source="open", price=to_price(quote.open)))
        return found

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
    ) -> bool:
        """Match ``order`` against ``observations`` and book the fill. True if filled."""
        etf = symbol_is_etf(order.symbol)
        quote = view.quote
        reference = resolve_reference(
            order.session_date,
            quote_reference=(D(str(quote.reference)) if quote and quote.reference else None),
            quote_price=(D(str(quote.price)) if quote else None),
            quote_date=view.quote_date,
            official_close=view.official_close,
            official_date=view.official_date,
        )
        if reference is None:
            return False
        limit_down, limit_up = limits_for(reference, etf)
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
        fills = await repo.list_paper_fills(account.id, epoch=account.epoch)
        book = replay(fills)
        day = order.session_date
        dt_qty = 0
        if order.side == "sell":
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
        )
        if order.side == "buy":
            cash = await repo.paper_cash(account.id, account.epoch)
            if cash < amounts.amount + amounts.commission:
                if await repo.finish_paper_order(
                    order.id, status="rejected", now=now, reject_reason="成交時資金不足，委託作廢"
                ):
                    stats.rejected += 1
                return False
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
        if await repo.apply_paper_fill(fill, ledger, now=now):
            stats.matched += 1
            return True
        return False

    # --- after the close -----------------------------------------------------------

    async def reconcile(
        self, repo: PaperRepository, now: datetime, stats: TickStats | None = None
    ) -> TickStats:
        """Replay each finished session's minute bars against its still-working orders.

        Catches touches the 3-second poll missed, then expires what never traded. Runs
        once official minutes are stored (after ~14:40), or at 16:00 with whatever was
        observed. Idempotent: booking and expiry both require ``working``.
        """
        stats = stats or TickStats()
        local_now = local(now)
        today = local_now.date()
        working = await repo.list_working_paper_orders()
        due_days = sorted({o.session_date for o in working if o.session_date <= today})
        accounts: dict[UUID, PaperAccountRecord | None] = {}
        for day in due_days:
            if day == today and local_now.time() < RECONCILE_AFTER:
                continue
            orders = [o for o in working if o.session_date == day]
            symbols = sorted({o.symbol for o in orders})
            views = await load_market(repo, symbols)
            first, last = session_window(day)
            bars_by_symbol = {
                symbol: await repo.list_minute_bars(
                    symbol, since=first, until=last + timedelta(minutes=1)
                )
                for symbol in symbols
            }
            final = day < today or local_now.time() >= FALLBACK_EXPIRE_AT
            for order in orders:
                bars = bars_by_symbol.get(order.symbol, [])
                complete = any(
                    b.official and local(b.minute).time() >= OFFICIAL_LAST_MINUTE for b in bars
                )
                if not (complete or final):
                    continue
                view = views.get(order.symbol)
                filled = False
                if view is not None and view.known:
                    observations = [self.bar_observation(b) for b in bars]
                    observations += self.quote_observations(view, order)
                    if not bars and view.official_date == day and view.official_open:
                        first_trade, _ = session_window(day)
                        if order.created_at < first_trade:
                            observations.append(
                                Observation(
                                    time=first_trade,
                                    source="open",
                                    price=to_price(view.official_open),
                                )
                            )
                    filled = await self.settle(
                        repo, order, view, observations, now, stats, accounts
                    )
                if not filled and await repo.finish_paper_order(
                    order.id, status="expired", now=now
                ):
                    stats.expired += 1
        return stats

    # --- daily snapshots -----------------------------------------------------------

    async def write_daily_snapshots(
        self, repo: PaperRepository, now: datetime, stats: TickStats | None = None
    ) -> TickStats:
        """Upsert one equity snapshot per account for the latest finished trading day.

        Runs from 14:45, retried every 15 minutes until every position is valued at the
        official close (or no position needs one). Safe to repeat: it is an upsert.
        """
        stats = stats or TickStats()
        local_now = local(now)
        today = local_now.date()
        if is_trading_day(today) and local_now.time() >= SNAPSHOT_AFTER:
            day = today
        else:
            day = previous_trading_day(today)
        state = self._snapshot_state.get(day)
        if state == "final":
            return stats
        if isinstance(state, datetime) and (now - state).total_seconds() < SNAPSHOT_RETRY_SECONDS:
            return stats
        self._snapshot_state[day] = now
        all_official = True
        for account in await repo.list_active_paper_accounts():
            if account_start_date(account) > day:
                continue
            snapshot, official = await self.snapshot_account(repo, account, day, now)
            await repo.upsert_paper_snapshot(snapshot)
            stats.equity_rows += 1
            all_official = all_official and official
        if all_official:
            self._snapshot_state[day] = "final"
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
        rows = (
            await repo.list_market_stock_rows([p.symbol for p in open_positions], [day])
            if open_positions
            else []
        )
        closes = {r.symbol: to_price(r.close) for r in rows}
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
