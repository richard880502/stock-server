"""Paper trading end to end on the in-memory repository: placement, matching, books."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from paper_support import (
    FRI,
    MON,
    TUE,
    Clock,
    FakeDemand,
    at,
    bar,
    market_repo,
    new_user,
    quote,
    stock_row,
    taiex,
)

from quant_signal.application.paper_jobs import PaperJobs, TickStats
from quant_signal.application.paper_market import load_market
from quant_signal.application.paper_trading import (
    PaperConflict,
    PaperInvalid,
    PaperNotFound,
    PaperService,
)
from quant_signal.domain.paper import AccountCreate, AccountPatch, OrderRequest

D = Decimal


def req(side="buy", kind="market", lots=1, limit=None, symbol="1101.TW"):
    return OrderRequest(
        symbol=symbol,
        side=side,
        order_type=kind,
        limit_price=None if limit is None else D(limit),
        lots=lots,
    )


async def setup(now, **account):
    repo = await market_repo()
    clock = Clock(now)
    service = PaperService(repo, clock=clock)
    user = new_user()
    summary = await service.create_account(
        user, AccountCreate(name="主帳戶", initial_cash=1_000_000, **account)
    )
    return repo, clock, service, PaperJobs(), user, summary.id


async def test_three_trades_cash_fees_and_pnl_by_hand() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 9, 30))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 29, 50))

    # Trade 1: market buy 2 lots. First tick after the order: 100 -> pays 100.5.
    t1 = await service.place_order(user, acct, req(lots=2))
    assert t1.status == "working" and t1.reserved_cash == 220_313  # 220,000 + floor(313.5)
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 30, 5))
    await jobs.tick(repo, at(FRI, 9, 30, 6))
    # 2,000 x 100.5 = 201,000; commission floor(286.425) = 286
    clock.now = at(FRI, 10, 0)

    # Trade 2: limit buy 1 lot at 101, touched by a tick at 101 -> fills at the limit.
    t2 = await service.place_order(user, acct, req(kind="limit", limit="101"))
    assert t2.reserved_cash == 101_143  # 101,000 + floor(143.925)
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 101, at(FRI, 10, 0, 10))
    await jobs.tick(repo, at(FRI, 10, 0, 11))
    # 1,000 x 101 = 101,000; commission 143
    acct_now = await service.get_account(user, acct)
    assert acct_now.cash == 1_000_000 - 201_286 - 101_143 == 697_571
    assert acct_now.available_cash == 697_571 and acct_now.working_orders == 0
    assert acct_now.positions[0].shares == 3000 and acct_now.positions[0].cost_basis == 302_429

    # Trade 3 (next session): market sell 1 lot at a 105 tick -> 104.5.
    clock.now = at(MON, 9, 20)
    repo.quotes_latest["1101.TW"] = quote(
        "1101.TW", 105, at(MON, 9, 20, 30), reference=100.5, open_=101
    )
    t3 = await service.place_order(user, acct, req(side="sell"))
    assert t3.session_date == MON
    repo.quotes_latest["1101.TW"] = quote(
        "1101.TW", 105, at(MON, 9, 21, 0), reference=100.5, open_=101
    )
    await jobs.tick(repo, at(MON, 9, 21, 1))
    fills = (await service.list_fills(user, acct)).fills
    sell = fills[0]
    # 1,000 x 104.5 = 104,500; commission floor(148.9125) = 148; tax floor(313.5) = 313
    assert (sell.amount, sell.commission, sell.tax, sell.price) == (104_500, 148, 313, 104.5)
    # removed cost = round(302,429 / 3) = 100,810 -> realised 104,039 - 100,810
    assert sell.realized_pnl == 3_229 and sell.day_trade is False

    # close at 106 (official row) values the 2,000 left shares
    await repo.upsert_market_stock_days([stock_row("1101.TW", MON, close=106, name="台泥")])
    clock.now = at(MON, 15, 0)
    detail = await service.get_account(user, acct)
    assert detail.cash == 801_610
    assert detail.market_value == 212_000 and detail.equity == 1_013_610
    assert detail.realized_pnl == 3_229
    assert detail.unrealized_pnl == 212_000 - 201_619 == 10_381
    assert detail.total_pnl == 13_610 == detail.realized_pnl + detail.unrealized_pnl
    assert detail.total_return_pct == 1.36
    pos = detail.positions[0]
    assert (pos.shares, pos.lots, pos.avg_cost, pos.price) == (2000, 2, 100.81, 106.0)
    assert pos.weight_pct == pytest.approx(20.92, abs=0.01)
    ledger = await repo.list_paper_ledger(acct, 0)
    assert sum(e.amount for e in ledger) == detail.cash


async def test_reserve_is_held_then_released_on_cancel() -> None:
    repo, _, service, _, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    order = await service.place_order(user, acct, req(kind="limit", limit="100"))
    assert order.reserved_cash == 100_142  # 100,000 + floor(142.5)
    summary = (await service.list_accounts(user))[0]
    assert summary.cash == 1_000_000 and summary.available_cash == 1_000_000 - 100_142
    cancelled = await service.cancel_order(user, acct, order.id)
    assert cancelled.status == "cancelled" and cancelled.reserved_cash == 0
    assert (await service.list_accounts(user))[0].available_cash == 1_000_000
    with pytest.raises(PaperConflict):
        await service.cancel_order(user, acct, order.id)


async def test_rejections_are_stored_with_reasons() -> None:
    repo, _, service, _, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    cases = [
        (req(kind="limit", limit="100.3"), "升降單位"),
        (req(kind="limit", limit="120"), "漲跌幅"),
        (req(lots=5000), "可用資金不足"),
        (req(side="sell"), "可賣股數不足"),
        (req(symbol="9999.TW"), "找不到股票"),
    ]
    for request, reason in cases:
        order = await service.place_order(user, acct, request)
        assert order.status == "rejected" and reason in order.reject_reason, request
        assert order.reserved_cash == 0
    listed = await service.list_orders(user, acct, status="done")
    assert len(listed.orders) == len(cases)
    assert (await service.list_orders(user, acct, status="working")).orders == []
    with pytest.raises(PaperInvalid):
        await service.place_order(user, acct, req(kind="limit"))  # no price
    with pytest.raises(PaperInvalid):
        await service.place_order(user, acct, req(symbol="abc"))


async def test_no_selling_shares_bought_today_unless_day_trade_enabled() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    await service.place_order(user, acct, req(lots=2))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 10, 0, 5))
    await jobs.tick(repo, at(FRI, 10, 0, 6))
    blocked = await service.place_order(user, acct, req(side="sell"))
    assert blocked.status == "rejected" and "當日買進" in blocked.reject_reason
    await service.update_account(user, acct, AccountPatch(allow_day_trade=True))
    allowed = await service.place_order(user, acct, req(side="sell"))
    assert allowed.status == "working"
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 102, at(FRI, 10, 5, 0))
    await jobs.tick(repo, at(FRI, 10, 5, 1))
    sell = (await service.list_fills(user, acct)).fills[0]
    # 1,000 x 101.5 = 101,500; day-trade tax floor(101,500 x 0.0015) = 152
    assert sell.day_trade and sell.tax == 152 and sell.price == 101.5


async def test_etf_sell_uses_the_etf_tax_rate() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["0050.TW"] = quote("0050.TW", 150, at(FRI, 9, 59), reference=150)
    await service.place_order(user, acct, req(symbol="0050.TW"))
    repo.quotes_latest["0050.TW"] = quote("0050.TW", 150, at(FRI, 10, 0, 5), reference=150)
    await jobs.tick(repo, at(FRI, 10, 0, 6))
    clock.now = at(MON, 10, 0)
    repo.quotes_latest["0050.TW"] = quote("0050.TW", 160, at(MON, 9, 59), reference=150)
    await service.place_order(user, acct, req(symbol="0050.TW", side="sell"))
    repo.quotes_latest["0050.TW"] = quote("0050.TW", 160, at(MON, 10, 0, 5), reference=150)
    await jobs.tick(repo, at(MON, 10, 0, 6))
    sell = (await service.list_fills(user, acct)).fills[0]
    # buy 150.05 (ETF tick 0.05); sell 159.95: amount 159,950, tax floor(159.95) = 159
    assert sell.price == 159.95 and sell.amount == 159_950 and sell.tax == 159


async def test_queued_order_waits_for_the_next_session_open() -> None:
    repo, _, service, jobs, user, acct = await setup(at(FRI, 20, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 13, 30))
    order = await service.place_order(user, acct, req(kind="limit", limit="101"))
    assert order.session_date == MON and order.status == "working"
    await jobs.tick(repo, at(FRI, 20, 0, 5))  # out of session: nothing happens
    assert (await service.list_orders(user, acct, status="working")).orders[0].id == order.id
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 103, at(MON, 9, 0, 30), open_=100)
    await jobs.tick(repo, at(MON, 9, 0, 31))
    filled = (await service.list_orders(user, acct)).orders[0]
    assert filled.status == "filled"
    assert filled.fill.price_source == "open" and filled.fill.price == 100.5


class StaleListing:
    """Repository proxy: the matcher reads the working orders, then waits until the test
    says go, so the user's cancel can commit while the matcher still holds the stale order."""

    def __init__(self, repo) -> None:
        self._repo = repo
        self.listed = asyncio.Event()
        self.release = asyncio.Event()
        self.stale: list = []

    def __getattr__(self, name):
        return getattr(self._repo, name)

    async def list_working_paper_orders(self, account_id=None):
        found = await self._repo.list_working_paper_orders(account_id)
        if account_id is None and not self.listed.is_set():
            self.stale = [o.model_copy() for o in found]
            self.listed.set()
            await self.release.wait()
        return found


async def test_matching_and_cancelling_race_never_double_books() -> None:
    repo, _, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    order = await service.place_order(user, acct, req())
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 10, 0, 5))
    # two matchers and a retry may overlap: still exactly one booking
    await asyncio.gather(jobs.tick(repo, at(FRI, 10, 0, 6)), jobs.tick(repo, at(FRI, 10, 0, 6)))
    await jobs.tick(repo, at(FRI, 10, 0, 9))
    assert len(await repo.list_paper_fills(acct, epoch=0)) == 1
    ledger = await repo.list_paper_ledger(acct, 0)
    assert [e.kind for e in ledger].count("buy") == 1
    assert await repo.paper_cash(acct, 0) == 1_000_000 - 100_500 - 143
    with pytest.raises(PaperConflict):  # cancel after the fill
        await service.cancel_order(user, acct, order.id)

    # The matcher lists the working order; the cancel commits; only then does the matcher,
    # still holding the stale "working" copy and a price that fills it, try to book.
    other = await service.place_order(user, acct, req())
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 10, 1, 5))
    proxy = StaleListing(repo)
    matcher = asyncio.create_task(PaperJobs().tick(proxy, at(FRI, 10, 1, 6)))
    await proxy.listed.wait()
    assert [o.id for o in proxy.stale] == [other.id] and proxy.stale[0].status == "working"
    await service.cancel_order(user, acct, other.id)  # commits while the matcher waits
    proxy.release.set()
    stats = await matcher
    assert stats.matched == 0 and stats.rejected == 0
    assert (await repo.get_paper_order(other.id)).status == "cancelled"
    assert len(await repo.list_paper_fills(acct, epoch=0)) == 1
    assert await repo.paper_cash(acct, 0) == 1_000_000 - 100_500 - 143
    # and the stale object itself, handed straight to the settle path, books nothing either
    view = (await load_market(repo, ["1101.TW"]))["1101.TW"]
    obs = PaperJobs.quote_observations(view, proxy.stale[0])
    assert obs  # the price would fill it...
    jobs_stats = TickStats()
    assert not await jobs.settle(repo, proxy.stale[0], view, obs, at(FRI, 10, 2), jobs_stats, {})
    assert jobs_stats.matched == 0 and len(await repo.list_paper_fills(acct, epoch=0)) == 1


async def test_apply_fill_is_idempotent() -> None:
    from quant_signal.application.paper_jobs import ledger_entries
    from quant_signal.domain.paper import PaperFillRecord

    repo, _, service, _, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    order = await service.place_order(user, acct, req())
    fill = PaperFillRecord(
        order_id=order.id,
        account_id=acct,
        symbol="1101.TW",
        side="buy",
        shares=1000,
        price=D("100.5"),
        amount=100_500,
        commission=143,
        tax=0,
        filled_at=at(FRI, 10, 0, 5),
        price_source="tick",
        price_time=at(FRI, 10, 0, 5),
    )
    entries = ledger_entries(fill, at(FRI, 10, 0, 6))
    assert await repo.apply_paper_fill(fill, entries, now=at(FRI, 10, 0, 6)) is True
    again = fill.model_copy(update={"id": uuid4()})
    entries = ledger_entries(again, at(FRI, 10, 0, 7))
    assert await repo.apply_paper_fill(again, entries, now=at(FRI, 10, 0, 7)) is False
    assert await repo.paper_cash(acct, 0) == 1_000_000 - 100_643


async def test_reconcile_fills_missed_touches_then_expires_the_rest() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 105, at(FRI, 9, 59))
    touched = await service.place_order(user, acct, req(kind="limit", limit="100"))
    never = await service.place_order(user, acct, req(kind="limit", limit="95"))
    assert (await service.list_accounts(user))[0].available_cash < 1_000_000 - 190_000
    # the poll never saw 100: only the official minute bars show it
    repo.minute_bars["1101.TW"] = [
        bar("1101.TW", at(FRI, 10, 5), 104, 104, 103, 104, official=True),
        bar("1101.TW", at(FRI, 11, 30), 101, 101, 99.9, 100, official=True),
        bar("1101.TW", at(FRI, 13, 24), 101, 101, 100.5, 101, official=True),
    ]
    await jobs.tick(repo, at(FRI, 13, 40))  # official bars not trusted before 14:40
    assert len((await service.list_orders(user, acct, status="working")).orders) == 2
    stats = await jobs.reconcile(repo, at(FRI, 14, 41))
    assert stats.expired == 1
    orders = {o.id: o for o in (await service.list_orders(user, acct)).orders}
    assert orders[touched.id].status == "filled"
    assert orders[touched.id].fill.price_source == "minute_bar"
    assert orders[touched.id].fill.price == 100  # opened above, touched down: at the limit
    assert orders[never.id].status == "expired" and orders[never.id].reserved_cash == 0
    summary = (await service.list_accounts(user))[0]
    assert summary.working_orders == 0 and summary.available_cash == summary.cash
    again = await jobs.reconcile(repo, at(FRI, 14, 42))  # re-run: nothing to do
    assert (again.expired, again.matched) == (0, 0)


async def test_unfilled_orders_expire_at_four_without_official_bars() -> None:
    repo, _, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 105, at(FRI, 9, 59))
    await service.place_order(user, acct, req(kind="limit", limit="95"))
    await jobs.reconcile(repo, at(FRI, 15, 0))
    assert len((await service.list_orders(user, acct, status="working")).orders) == 1
    await jobs.reconcile(repo, at(FRI, 16, 1))
    assert (await service.list_orders(user, acct, status="done")).orders[0].status == "expired"


async def test_reset_starts_a_new_epoch_and_keeps_history() -> None:
    repo, _, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    await service.place_order(user, acct, req())
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 10, 0, 5))
    await jobs.tick(repo, at(FRI, 10, 0, 6))
    pending = await service.place_order(user, acct, req(kind="limit", limit="90"))
    summary = await service.reset_account(user, acct)
    assert summary.cash == 1_000_000 and summary.positions_count == 0
    assert summary.working_orders == 0 and summary.total_pnl == 0
    assert (await repo.get_paper_order(pending.id)).status == "cancelled"
    assert (await service.list_fills(user, acct)).fills == []
    assert len((await service.list_fills(user, acct, include_archived=True)).fills) == 1
    assert (await service.list_orders(user, acct)).orders == []
    assert len((await service.list_orders(user, acct, include_archived=True)).orders) == 2


async def test_account_rules_max_three_unique_names_and_archive() -> None:
    repo, _, service, _, user, _ = await setup(at(FRI, 10, 0))
    await service.create_account(user, AccountCreate(name="B"))
    await service.create_account(user, AccountCreate(name="C"))
    with pytest.raises(PaperConflict, match="最多 3"):
        await service.create_account(user, AccountCreate(name="D"))
    other = new_user()
    await service.create_account(other, AccountCreate(name="主帳戶"))  # names are per user
    named = {a.name: a.id for a in await service.list_accounts(user)}
    with pytest.raises(PaperConflict, match="已有名為"):
        await service.update_account(user, named["B"], AccountPatch(name="主帳戶"))
    await service.archive_account(user, named["C"])
    with pytest.raises(PaperNotFound):
        await service.get_account(user, named["C"])
    await service.create_account(user, AccountCreate(name="C"))  # archived slot and name free


async def test_demand_respects_the_shared_cap_and_priorities() -> None:
    repo, _, service, _, user, acct = await setup(at(FRI, 10, 0))
    for symbol, name in (("2330.TW", "台積電"), ("2317.TW", "鴻海")):
        await repo.upsert_market_stock_days(
            [stock_row(symbol, d, close=100, name=name) for d in (FRI,)]
        )
    for symbol in ("1101.TW", "2330.TW", "2317.TW"):
        repo.quotes_latest[symbol] = quote(symbol, 100, at(FRI, 9, 59))
    # a position in 0050 (lower priority than working orders)
    repo.quotes_latest["0050.TW"] = quote("0050.TW", 150, at(FRI, 9, 59), reference=150)
    await service.place_order(user, acct, req(symbol="0050.TW"))
    repo.quotes_latest["0050.TW"] = quote("0050.TW", 150, at(FRI, 10, 0, 5), reference=150)
    plain = PaperJobs()
    await plain.tick(repo, at(FRI, 10, 0, 6))
    for symbol in ("1101.TW", "2330.TW", "2317.TW"):
        await service.place_order(user, acct, req(kind="limit", limit="90", symbol=symbol))

    full = FakeDemand(capacity=3, preloaded=("9999.TW",))  # viewers already hold one seat
    jobs = PaperJobs(demand=full)
    stats = await jobs.tick(repo, at(FRI, 10, 1))
    assert len(stats.demand) == 2 and "0050.TW" not in stats.demand  # orders first, cap kept
    assert len(full.symbols) == 3

    roomy = FakeDemand(capacity=10)
    capped = PaperJobs(demand=roomy, demand_max=3)
    stats = await capped.tick(repo, at(FRI, 10, 1))
    assert sorted(stats.demand) == ["1101.TW", "2317.TW", "2330.TW"]  # paper's own ceiling
    outside = await PaperJobs(demand=FakeDemand(10)).tick(repo, at(FRI, 20, 0))
    assert outside.demand == []  # no demand outside the session window


async def test_daily_snapshot_is_idempotent_and_upgrades_to_the_official_close() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    repo.market_observations["TW"] = [taiex(FRI, 27_000.0)]
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    await service.place_order(user, acct, req(lots=2))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 10, 0, 5))
    await jobs.tick(repo, at(FRI, 10, 0, 6))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 103, at(FRI, 13, 30))
    await jobs.tick(repo, at(FRI, 14, 50))
    first = (await repo.list_paper_snapshots(acct, epoch=0))[0]
    assert first.trading_date == FRI and first.benchmark_close == 27_000.0
    cash = 1_000_000 - 201_000 - 286
    assert (first.cash, first.market_value, first.equity) == (cash, 206_000, cash + 206_000)
    await jobs.tick(repo, at(FRI, 14, 51))  # throttled: no new work
    await repo.upsert_market_stock_days([stock_row("1101.TW", FRI, close=104, name="台泥")])
    await jobs.tick(repo, at(FRI, 15, 10))  # >15 min later: official close lands
    snaps = await repo.list_paper_snapshots(acct, epoch=0)
    assert len(snaps) == 1 and snaps[0].market_value == 208_000
    await jobs.tick(repo, at(FRI, 15, 30))
    assert (await repo.list_paper_snapshots(acct, epoch=0))[0].market_value == 208_000
    assert len(await repo.list_paper_ledger(acct, 0)) == 3  # deposit, buy, commission only

    clock.now = at(TUE, 11, 0)
    curve = await service.equity(user, acct, days=365)
    assert curve.points[0].date == FRI and not curve.points[0].live
    assert curve.points[-1].live and curve.points[-1].date == TUE
