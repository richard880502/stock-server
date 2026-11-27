"""Regression tests for the reviewed paper-trading defects (snapshots, past sessions,
event-loop hygiene, demand, input bounds, closing print, ETF limits, reserves, epochs)."""

from __future__ import annotations

import asyncio
import time as _time
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from paper_support import (
    FRI,
    MON,
    THU,
    Clock,
    FakeDemand,
    at,
    bar,
    market_repo,
    new_user,
    quote,
    stock_row,
)
from pydantic import ValidationError

from quant_signal.application.paper_jobs import PaperJobs, TickStats, ledger_entries
from quant_signal.application.paper_ports import PaperInsufficientCash
from quant_signal.application.paper_trading import PaperConflict, PaperService, paper_rules
from quant_signal.domain.paper import (
    AccountCreate,
    AccountPatch,
    OrderRequest,
    PaperFillRecord,
    PaperOrderRecord,
)
from quant_signal.domain.tw_trading import (
    commission,
    price_limit_pct_for,
    price_limits,
)
from quant_signal.quant.paper_calendar import matcher_open
from quant_signal.quant.paper_matching import (
    Match,
    Observation,
    OrderSpec,
    match_order,
    past_session_reference,
)

D = Decimal


def req(side="buy", kind="market", lots=1, limit=None, symbol="1101.TW"):
    return OrderRequest(
        symbol=symbol,
        side=side,
        order_type=kind,
        limit_price=None if limit is None else D(limit),
        lots=lots,
    )


async def setup(now, *, repo=None, **account):
    repo = repo or await market_repo()
    clock = Clock(now)
    service = PaperService(repo, clock=clock)
    user = new_user()
    summary = await service.create_account(
        user,
        AccountCreate(
            name="主帳戶", initial_cash=account.pop("initial_cash", 1_000_000), **account
        ),
    )
    return repo, clock, service, user, summary.id


class Spy:
    """Wraps a repository and records every method called on it."""

    def __init__(self, repo) -> None:
        self._repo = repo
        self.calls: list[str] = []

    def __getattr__(self, name):
        target = getattr(self._repo, name)
        if not callable(target):
            return target

        async def call(*args, **kwargs):
            self.calls.append(name)
            return await target(*args, **kwargs)

        return call


async def progress_during(work) -> int:
    """How many times a concurrent task got to run while ``work`` was running."""
    ticks = 0
    stop = False

    async def ticker() -> None:
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0)

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    try:
        await work
    finally:
        stop = True
        await task
    return ticks


# --- 1. snapshots are only final after reconciliation -----------------------------


async def _snapshot_scenario():
    repo, clock, service, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 101, at(FRI, 9, 59))
    await service.place_order(user, acct, req(kind="limit", limit="99"))
    jobs = PaperJobs()
    await jobs.tick(repo, at(FRI, 10, 0, 5))
    # a live bar dips to 98.5 at 10:05; the 3 s poll missed it
    repo.minute_bars["1101.TW"] = [bar("1101.TW", at(FRI, 10, 5), 100, 100, 98.5, 99)]
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 105, at(FRI, 13, 30))
    await repo.upsert_market_stock_days(
        [stock_row("1101.TW", FRI, close=105.0, name="台泥", change=5)]
    )
    return repo, clock, service, user, acct, jobs


async def test_snapshot_is_not_final_before_the_days_fills_are_booked() -> None:
    repo, clock, service, user, acct, jobs = await _snapshot_scenario()
    await jobs.tick(repo, at(FRI, 14, 46))  # official close exists, order still working
    provisional = (await repo.list_paper_snapshots(acct, epoch=0))[0]
    assert provisional.equity == 1_000_000 and provisional.market_value == 0
    await jobs.tick(repo, at(FRI, 16, 0, 1))  # reconcile back-dates the fill into Friday
    final = (await repo.list_paper_snapshots(acct, epoch=0))[0]
    assert final.market_value == 105_000 and final.cash == 1_000_000 - 99_000 - 141
    assert final.equity == 1_005_859
    clock.now = at(MON, 9, 5)
    detail = await service.get_account(user, acct)
    assert detail.day_pnl == 0  # no price change since Friday's close: no phantom gain


async def test_reconcile_rewrites_the_snapshot_of_the_date_it_changed() -> None:
    repo, _, service, user, acct, jobs = await _snapshot_scenario()
    await jobs.tick(repo, at(FRI, 14, 46))
    assert (await repo.list_paper_snapshots(acct, epoch=0))[0].market_value == 0
    await jobs.reconcile(repo, at(FRI, 16, 0, 1))  # no tick: reconcile alone must rewrite
    snapshot = (await repo.list_paper_snapshots(acct, epoch=0))[0]
    assert snapshot.market_value == 105_000 and snapshot.equity == 1_005_859


async def test_snapshot_waits_while_orders_for_the_day_are_still_working() -> None:
    repo, _, _, _, acct, jobs = await _snapshot_scenario()
    await jobs.tick(repo, at(FRI, 14, 46))
    state = jobs._snapshot_state[FRI]
    assert state.final is False
    await jobs.tick(repo, at(FRI, 16, 0, 1))
    assert jobs._snapshot_state[FRI].final is True


# --- 2. an order of a past session is matched against that session -----------------


async def test_order_from_a_past_session_is_matched_not_expired() -> None:
    repo, clock, service, user, acct = await setup(at(THU, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 101, at(THU, 9, 59))
    placed = await service.place_order(user, acct, req(kind="limit", limit="99"))
    repo.minute_bars["1101.TW"] = [
        bar("1101.TW", at(THU, 10, 5), 100, 100, 98.5, 99, official=True),
        bar("1101.TW", at(THU, 13, 25), 100, 100, 100, 100, official=True),
    ]
    # ingest was down after 10:00 Thursday; Friday's quote is the only live price
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 102, at(FRI, 9, 29))
    stats = await PaperJobs().tick(repo, at(FRI, 9, 30))
    order = (await service.list_orders(user, acct)).orders[0]
    assert order.id == placed.id and order.status == "filled"
    assert order.fill.price == 99 and order.fill.price_source == "minute_bar"
    assert stats.matched == 1 and stats.expired == 0


def test_past_session_reference_prefers_the_days_own_row() -> None:
    assert past_session_reference(day_close=D("100"), day_change=D("2"), prior_close=D("97")) == D(
        "98.00"
    )
    assert past_session_reference(day_close=None, day_change=None, prior_close=D("97")) == D("97")
    assert past_session_reference(day_close=D("100"), day_change=D("100"), prior_close=None) is None


async def test_past_session_limit_band_comes_from_that_sessions_reference() -> None:
    repo, _, service, user, acct = await setup(at(THU, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(THU, 9, 59))
    await service.place_order(user, acct, req(kind="limit", limit="90"))
    # Thursday's official row says reference 100 (close 100, change 0): limit-down is 90.0
    repo.minute_bars["1101.TW"] = [
        bar("1101.TW", at(THU, 10, 5), 100, 100, 90, 95, official=True),
        bar("1101.TW", at(THU, 13, 25), 95, 95, 95, 95, official=True),
    ]
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 150, at(FRI, 9, 29), reference=150)
    await PaperJobs().tick(repo, at(FRI, 9, 30))
    assert (await service.list_orders(user, acct)).orders[0].status == "filled"


# --- 3. event-loop hygiene ------------------------------------------------------------


async def _many_orders(n_orders: int, n_symbols: int, *, bars: int = 260):
    repo = await market_repo()
    symbols = [f"{2000 + i}.TW" for i in range(n_symbols)]
    await repo.upsert_market_stock_days([stock_row(s, THU, close=100.0, name="x") for s in symbols])
    for s in symbols:
        repo.quotes_latest[s] = quote(s, 105, at(FRI, 13, 19))
        repo.minute_bars[s] = [
            bar(s, at(FRI, 9) + timedelta(minutes=m), 105, 106, 104, 105) for m in range(bars)
        ]
    account = uuid4()
    for i in range(n_orders):
        await repo.add_paper_order(
            PaperOrderRecord(
                account_id=account,
                symbol=symbols[i % n_symbols],
                name="x",
                side="buy",
                order_type="limit",
                limit_price=D("95"),
                lots=1,
                session_date=FRI,
                created_at=at(FRI, 9, 0, 1),
                updated_at=at(FRI, 9, 0, 1),
            )
        )
    return repo, symbols


async def test_live_matcher_yields_to_the_event_loop() -> None:
    repo, _ = await _many_orders(500, 100)
    jobs = PaperJobs()
    ticks = await progress_during(jobs.tick(repo, at(FRI, 13, 20, 1)))
    assert ticks >= 500 // 50 - 1


async def test_reconcile_yields_to_the_event_loop() -> None:
    repo, _ = await _many_orders(500, 100)
    jobs = PaperJobs()
    ticks = await progress_during(jobs.reconcile(repo, at(MON, 9, 30)))
    assert ticks >= 500 // 50 - 1


async def test_snapshots_and_demand_yield_to_the_event_loop() -> None:
    repo = await market_repo()
    service = PaperService(repo, clock=Clock(at(FRI, 10, 0)))
    for _ in range(120):
        await service.create_account(new_user(), AccountCreate(name="a"))
    jobs = PaperJobs()
    ticks = await progress_during(jobs.write_daily_snapshots(repo, at(FRI, 14, 50)))
    assert ticks >= 120 // 50 - 1 and len(jobs._snapshot_state[FRI].written) == 120
    repo, _ = await _many_orders(300, 150, bars=1)
    working = await repo.list_working_paper_orders()
    demand = PaperJobs(demand=FakeDemand(1000), demand_max=1000)
    ticks = await progress_during(demand.register_demand(repo, working, at(FRI, 10, 0)))
    assert ticks >= 150 // 50 - 1


async def test_a_tick_with_no_new_bars_does_no_per_order_bar_work(monkeypatch) -> None:
    repo, _ = await _many_orders(60, 6, bars=30)
    spy = Spy(repo)
    jobs = PaperJobs()
    await jobs.tick(spy, at(FRI, 9, 40, 1))
    assert spy.calls.count("list_minute_bars") == 6  # once per symbol, not per order
    spy.calls.clear()
    settled: list[int] = []
    real_settle = jobs.settle

    async def counting(*args, **kwargs):
        settled.append(1)
        return await real_settle(*args, **kwargs)

    monkeypatch.setattr(jobs, "settle", counting)
    built: list[int] = []
    real_obs = PaperJobs.bar_observation
    monkeypatch.setattr(
        PaperJobs, "bar_observation", staticmethod(lambda b: built.append(1) or real_obs(b))
    )
    await jobs.tick(spy, at(FRI, 9, 41, 1))  # next minute, but no bar is newer than the last
    assert settled == [] and built == []
    # only the cheap per-symbol reads happened: no order-by-order bar reads
    assert spy.calls.count("list_minute_bars") == 6
    # a bar appears: only that bar is read, and only for its symbol's orders
    spy.calls.clear()
    repo.minute_bars["2000.TW"].append(bar("2000.TW", at(FRI, 9, 40), 105, 106, 104, 105))
    await jobs.tick(spy, at(FRI, 9, 42, 1))
    assert len(settled) == 10  # the 10 orders of 2000.TW


async def test_watermark_only_reads_bars_after_the_last_one_checked() -> None:
    repo, _ = await _many_orders(2, 1, bars=10)
    seen: list = []
    real = repo.list_minute_bars

    async def spy(symbol, *, since, until):
        seen.append(since)
        return await real(symbol, since=since, until=until)

    repo.list_minute_bars = spy  # type: ignore[method-assign]
    jobs = PaperJobs()
    await jobs.tick(repo, at(FRI, 9, 30, 1))
    await jobs.tick(repo, at(FRI, 9, 31, 1))
    assert seen[0] == at(FRI, 9, 0)
    assert seen[1] == at(FRI, 9, 9) + timedelta(minutes=1)  # newest bar read was 09:09


# --- 4. demand registration is one aggregate ----------------------------------------


async def test_demand_uses_one_aggregate_and_no_fill_replay() -> None:
    repo, clock, service, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    await service.place_order(user, acct, req())
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 10, 0, 5))
    await PaperJobs().tick(repo, at(FRI, 10, 0, 6))  # a position in 1101 now exists
    for i in range(5):  # more accounts, none holding anything
        await service.create_account(new_user(), AccountCreate(name=f"n{i}"))
    spy = Spy(repo)
    demand = FakeDemand(10)
    stats = await PaperJobs(demand=demand).tick(spy, at(FRI, 10, 1))
    assert stats.demand == ["1101.TW"]
    assert "list_paper_fills" not in spy.calls
    assert spy.calls.count("list_open_position_symbols") == 1


async def test_open_position_symbols_sums_signed_shares_in_the_current_epoch() -> None:
    repo, _, service, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    await service.place_order(user, acct, req(lots=2))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 10, 0, 5))
    await PaperJobs().tick(repo, at(FRI, 10, 0, 6))
    assert await repo.list_open_position_symbols() == ["1101.TW"]
    await service.reset_account(user, acct)  # the old epoch no longer counts
    assert await repo.list_open_position_symbols() == []


async def test_idle_tick_with_no_orders_and_no_positions_is_cheap() -> None:
    repo, _, service, user, acct = await setup(at(FRI, 10, 0))
    jobs = PaperJobs(demand=FakeDemand(10))
    await jobs.tick(repo, at(FRI, 10, 0, 6))  # first tick also writes the prior-day snapshot
    spy = Spy(repo)
    stats = await jobs.tick(spy, at(FRI, 10, 0, 30))
    assert stats == TickStats()
    assert set(spy.calls) <= {"list_working_paper_orders", "list_open_position_symbols"}
    assert len(spy.calls) <= 2
    plain = PaperJobs()
    await plain.tick(repo, at(FRI, 10, 0, 6))
    spy = Spy(repo)
    start = _time.process_time()
    for i in range(200):
        await plain.tick(spy, at(FRI, 10, 1) + timedelta(seconds=3 * i))
    per_tick = (_time.process_time() - start) / 200
    assert per_tick < 0.001  # well under a millisecond of CPU per idle tick
    assert spy.calls.count("list_working_paper_orders") == 200 and len(spy.calls) == 200


# --- 5. input bounds --------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, needle",
    [
        ({"lots": 3_000_000_000}, "張數"),
        ({"lots": 0}, "張數"),
        ({"lots": 10_001}, "張數"),
        ({"lots": 1, "order_type": "limit", "limit_price": D("1e11")}, "限價"),
        ({"lots": 1, "order_type": "limit", "limit_price": D("100000")}, "限價"),
        ({"lots": 1, "order_type": "limit", "limit_price": D("0")}, "限價"),
        ({"lots": 1, "order_type": "limit", "limit_price": D("-5")}, "限價"),
        ({"lots": 1, "order_type": "limit", "limit_price": D("10.123")}, "2 位小數"),
    ],
)
def test_order_request_bounds_have_chinese_messages(kwargs, needle) -> None:
    body = {"symbol": "1101.TW", "side": "buy", "order_type": "market", **kwargs}
    with pytest.raises(ValidationError) as caught:
        OrderRequest(**body)
    assert needle in str(caught.value)


def test_order_request_accepts_the_edges() -> None:
    assert OrderRequest(symbol="1101.TW", side="buy", order_type="market", lots=10_000)
    ok = OrderRequest(
        symbol="1101.TW", side="buy", order_type="limit", lots=1, limit_price=D("99999.99")
    )
    assert ok.limit_price == D("99999.99")
    assert OrderRequest(
        symbol="1101.TW", side="buy", order_type="limit", lots=1, limit_price=D("99.50")
    )


def test_account_bounds() -> None:
    for bad in (
        {"initial_cash": 99_999},
        {"initial_cash": 10**15},
        {"commission_discount": D("0.05")},
        {"commission_discount": D("1.01")},
        {"commission_discount": D("0.12345")},
    ):
        with pytest.raises(ValidationError):
            AccountCreate(name="x", **bad)
    assert AccountCreate(name="x", commission_discount=D("0.1")).commission_discount == D("0.1")
    with pytest.raises(ValidationError, match="手續費折數"):
        AccountPatch(commission_discount=D("2"))


# --- 6. the closing-auction print -----------------------------------------------------


async def test_closing_print_after_1330_fills_a_live_order() -> None:
    repo, clock, service, user, acct = await setup(at(FRI, 13, 27))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 13, 26))
    placed = await service.place_order(user, acct, req())
    assert placed.session_date == FRI
    repo.quotes_latest["1101.TW"] = quote(
        "1101.TW", 100, at(FRI, 13, 30) + timedelta(milliseconds=400)
    )
    await PaperJobs().tick(repo, at(FRI, 13, 30, 3))
    order = (await service.list_orders(user, acct)).orders[0]
    assert order.status == "filled" and order.fill.price == D("100.5")


def test_matcher_stays_open_until_1335_but_orders_after_1330_are_next_session() -> None:
    assert matcher_open(at(FRI, 13, 35)) and not matcher_open(at(FRI, 13, 35, 1))
    assert matcher_open(at(FRI, 9, 0)) and not matcher_open(at(FRI, 8, 59))
    assert not matcher_open(at(FRI + timedelta(days=1), 10))
    from quant_signal.quant.paper_calendar import session_for_order

    assert session_for_order(at(FRI, 13, 30, 1)) == (MON, True)
    spec = OrderSpec("buy", "market", None, at(FRI, 13, 27), FRI)
    late = Observation(at(FRI, 13, 36), "tick", D("100"))
    assert match_order(spec, [late], limit_down=D(90), limit_up=D(110), slippage_ticks=1) is None


# --- 7. ETF price limits ---------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol, pct",
    [
        ("2330.TW", D("0.10")),
        ("6488.TWO", D("0.10")),
        ("2881A.TW", D("0.10")),
        ("00631L.TW", D("0.20")),
        ("00632R.TW", D("0.10")),
        ("0050.TW", None),
        ("00878.TW", None),
        ("00679B.TWO", None),
        ("006208.TW", None),
    ],
)
def test_price_limit_pct_by_symbol(symbol, pct) -> None:
    assert price_limit_pct_for(symbol) == pct


def test_leveraged_etf_band_is_twenty_percent() -> None:
    down, up = price_limits(D("100"), etf=True, pct=D("0.20"))
    assert (down, up) == (D("80.00"), D("120.00"))


async def test_limitless_etf_has_no_band_no_lock_and_a_last_x_110_reserve() -> None:
    repo, clock, service, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["0050.TW"] = quote("0050.TW", 150, at(FRI, 9, 59), reference=100)
    preview = await service.preview(user, acct, req(symbol="0050.TW"))
    assert preview.ok and preview.limit_up is None and preview.limit_down is None
    assert preview.reserve == 165_000 + commission(165_000)
    wild = await service.place_order(
        user,
        acct,
        req(kind="limit", limit="200", symbol="0050.TW"),  # +33% of 150: no band
    )
    assert wild.status == "working"
    market = await service.place_order(user, acct, req(symbol="0050.TW"))
    assert market.status == "working" and market.reserved_cash == 165_000 + commission(165_000)
    # a tick far above the old 10% band is not "locked": the orders fill
    repo.quotes_latest["0050.TW"] = quote("0050.TW", 190, at(FRI, 10, 0, 5), reference=100)
    stats = await PaperJobs().tick(repo, at(FRI, 10, 0, 6))
    assert stats.matched == 2


async def test_leveraged_and_inverse_etfs_use_their_own_band() -> None:
    repo = await market_repo()
    await repo.upsert_market_stock_days(
        [
            stock_row("00631L.TW", THU, close=100.0, name="元大台灣50正2"),
            stock_row("00632R.TW", THU, close=100.0, name="元大台灣50反1"),
        ]
    )
    for symbol in ("00631L.TW", "00632R.TW"):
        repo.quotes_latest[symbol] = quote(symbol, 100, at(FRI, 9, 59), reference=100)
    _, _, service, user, acct = await setup(at(FRI, 10, 0), repo=repo)
    lev = await service.preview(user, acct, req(symbol="00631L.TW"))
    inv = await service.preview(user, acct, req(symbol="00632R.TW"))
    assert (lev.limit_up, lev.limit_down) == (120.0, 80.0)
    assert (inv.limit_up, inv.limit_down) == (110.0, 90.0)
    over = await service.place_order(user, acct, req(kind="limit", limit="115", symbol="00632R.TW"))
    assert over.status == "rejected" and "漲跌幅" in over.reject_reason
    ok = await service.place_order(user, acct, req(kind="limit", limit="115", symbol="00631L.TW"))
    assert ok.status == "working"


def test_rules_document_the_etf_exception() -> None:
    notes = " ".join(paper_rules(1, 3)["notes"])
    assert "1.10" in notes and "ETF" in notes and "沒有漲跌幅" in notes


# --- 8. fills are tied to reserves --------------------------------------------------------


async def test_a_fill_cannot_eat_other_orders_reserves() -> None:
    repo, clock, service, user, acct = await setup(at(FRI, 10, 0), initial_cash=210_000)
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    first = await service.place_order(user, acct, req(kind="limit", limit="100"))
    second = await service.place_order(user, acct, req(kind="limit", limit="100"))
    assert first.status == second.status == "working"
    # a 110 fill of the first order costs more than its own reserve (100,142)
    record = await repo.get_paper_order(first.id)
    fill = PaperFillRecord(
        order_id=first.id,
        account_id=acct,
        symbol="1101.TW",
        side="buy",
        shares=1000,
        price=D("110"),
        amount=110_000,
        commission=156,
        tax=0,
        filled_at=at(FRI, 10, 1),
        price_source="tick",
        price_time=at(FRI, 10, 1),
    )
    assert record is not None
    with pytest.raises(PaperInsufficientCash):
        await repo.apply_paper_fill(fill, ledger_entries(fill, at(FRI, 10, 1)), now=at(FRI, 10, 1))
    assert await repo.list_paper_fills(acct, epoch=0) == []
    assert (await repo.get_paper_order(first.id)).status == "working"
    assert (await repo.get_paper_order(second.id)).reserved_cash == 100_142


async def test_matcher_rejects_a_fill_that_would_eat_other_reserves() -> None:
    repo, clock, service, user, acct = await setup(at(FRI, 10, 0), initial_cash=210_000)
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    first = await service.place_order(user, acct, req(kind="limit", limit="100"))
    second = await service.place_order(user, acct, req(kind="limit", limit="100"))
    account = await repo.get_paper_account(acct)
    order = await repo.get_paper_order(first.id)
    stats = TickStats()
    found = Match(price=D("110"), time=at(FRI, 10, 1), source="tick")
    assert await PaperJobs().book(repo, account, order, found, at(FRI, 10, 1), stats) is False
    assert stats.rejected == 1 and stats.matched == 0
    stored = await repo.get_paper_order(first.id)
    assert stored.status == "rejected" and "資金不足" in stored.reject_reason
    other = await repo.get_paper_order(second.id)
    assert other.status == "working" and other.reserved_cash == 100_142


async def test_raising_the_discount_resizes_reserves_or_returns_409() -> None:
    repo, clock, service, user, acct = await setup(
        at(FRI, 10, 0), initial_cash=200_200, commission_discount=D("0.6")
    )
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    a = await service.place_order(user, acct, req(kind="limit", limit="100"))
    b = await service.place_order(user, acct, req(kind="limit", limit="100"))
    assert a.reserved_cash == b.reserved_cash == 100_000 + commission(100_000, _cfg("0.6"))
    with pytest.raises(PaperConflict, match="可用資金不足"):
        await service.update_account(user, acct, AccountPatch(commission_discount=D("1.0")))
    assert (await repo.get_paper_account(acct)).commission_discount == D("0.6")
    assert (await repo.get_paper_order(a.id)).reserved_cash == a.reserved_cash
    summary = await service.update_account(user, acct, AccountPatch(commission_discount=D("0.61")))
    assert summary.commission_discount == 0.61
    expected = 100_000 + commission(100_000, _cfg("0.61"))
    assert (await repo.get_paper_order(a.id)).reserved_cash == expected
    assert summary.available_cash == 200_200 - 2 * expected
    cheaper = await service.update_account(user, acct, AccountPatch(commission_discount=D("0.5")))
    assert cheaper.available_cash > summary.available_cash


def _cfg(discount: str):
    from quant_signal.domain.tw_trading import TWCostConfig

    return TWCostConfig(commission_discount=D(discount))


async def test_account_valuation_reads_cash_and_working_orders_together() -> None:
    repo, _, service, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    await service.place_order(user, acct, req(kind="limit", limit="100"))
    spy = Spy(repo)
    await PaperService(spy, clock=Clock(at(FRI, 10, 0))).get_account(user, acct)
    assert "paper_cash_and_working" in spy.calls
    assert "paper_cash" not in spy.calls


# --- 9. placement re-reads the account after taking the lock ---------------------------


async def test_order_placed_while_a_reset_waits_on_the_lock_uses_the_new_epoch() -> None:
    repo, clock, service, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    real_lock = repo.lock_paper_account

    async def lock_after_reset(account_id):
        await real_lock(account_id)
        await repo.reset_paper_account(account_id, now=at(FRI, 10, 0, 1))  # won the race

    repo.lock_paper_account = lock_after_reset  # type: ignore[method-assign]
    order = await service.place_order(user, acct, req(kind="limit", limit="100"))
    stored = await repo.get_paper_order(order.id)
    assert stored.epoch == (await repo.get_paper_account(acct)).epoch == 1


# --- 10. snapshots are per epoch ------------------------------------------------------------


async def test_reset_after_the_snapshot_still_gets_a_row_and_keeps_the_old_one() -> None:
    repo, clock, service, user, acct = await setup(at(FRI, 10, 0))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    await service.place_order(user, acct, req(lots=2))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 10, 0, 5))
    jobs = PaperJobs()
    await jobs.tick(repo, at(FRI, 10, 0, 6))
    await repo.upsert_market_stock_days(
        [stock_row("1101.TW", FRI, close=105.0, name="台泥", change=5)]
    )
    await jobs.tick(repo, at(FRI, 14, 50))
    old = (await repo.list_paper_snapshots(acct, epoch=0))[0]
    assert old.market_value == 210_000 and jobs._snapshot_state[FRI].final
    clock.now = at(FRI, 14, 55)
    await service.reset_account(user, acct)
    await jobs.tick(repo, at(FRI, 15, 10))  # >15 min after the first check
    fresh = await repo.list_paper_snapshots(acct, epoch=1)
    assert [(s.trading_date, s.equity, s.market_value) for s in fresh] == [(FRI, 1_000_000, 0)]
    assert (await repo.list_paper_snapshots(acct, epoch=0))[0] == old  # not overwritten
    # a restarted matcher recomputes the same keys without touching the other epoch
    again = PaperJobs()
    await again.tick(repo, at(FRI, 16, 30))
    assert (await repo.list_paper_snapshots(acct, epoch=0))[0] == old
