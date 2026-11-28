"""Time-weighted chaining with transfers across missing snapshot days, after the close,
over weekends and around resets: every step's flow comes from the ledger, and the latest
session day is one live step ``(E_now - F_post) / (E_prev + F_in)``. In-memory repository.

``frontend/features/paper/live-parity.fixture.json`` is written from the scenarios here
(``UPDATE_PAPER_PARITY=1``) and read by ``paper-math.test.ts``: the client's live
re-pricing must land on the server's figures in every phase of the day."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest
from paper_support import FRI, MON, THU, TUE, at, quote, taiex
from test_paper_dividends import make_action
from test_paper_transfers import SAT, setup, xfer

from quant_signal.domain.paper import OrderRequest, ResetRequest

WED = date(2026, 12, 2)
SUN = date(2026, 11, 29)
PARITY = Path(__file__).parents[1] / "frontend/features/paper/live-parity.fixture.json"


def pct(value: float) -> float:
    return round(value * 100, 2)


async def friday_gain(*, thursday_row=True, friday_row=True, lots=5, close=120):
    """Account opened Thursday with 1,000,000; Friday buys ``lots`` at 100 (+fees) and the
    stock closes at ``close``. Returns (ctx, equity at Friday's close)."""
    repo, clock, service, jobs, user, acct = await setup(at(THU, 10, 0))
    if thursday_row:
        await jobs.tick(repo, at(THU, 14, 50))
    clock.now = at(FRI, 10, 0)
    order = OrderRequest(symbol="1101.TW", side="buy", order_type="market", lots=lots)
    await service.place_order(user, acct, order)
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 10, 0, 5))
    await jobs.tick(repo, at(FRI, 10, 0, 6))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", close, at(FRI, 13, 29))
    if friday_row:
        await jobs.tick(repo, at(FRI, 14, 50))
    clock.now = at(FRI, 14, 55)
    e_fri = (await service.get_account(user, acct)).equity
    return (repo, clock, service, jobs, user, acct), e_fri


# --- 1. a day without a snapshot ---------------------------------------------------------


async def test_a_missing_snapshot_day_keeps_its_deposit_a_deposit() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    await jobs.tick(repo, at(FRI, 14, 50))
    clock.now = at(MON, 10, 0)
    await service.transfer(user, acct, xfer("in", 1_000_000))
    # outage: no Monday row; Tuesday's job writes Tuesday only
    await jobs.tick(repo, at(TUE, 14, 50))
    snaps = await repo.list_paper_snapshots(acct, epoch=0)
    assert [(s.trading_date, s.equity) for s in snaps] == [(FRI, 1_000_000), (TUE, 2_000_000)]
    clock.now = at(WED, 10, 0)
    summary = await service.get_account(user, acct)
    assert (summary.total_return_pct, summary.total_pnl, summary.day_pnl) == (0.0, 0, 0)
    curve = await service.equity(user, acct)
    assert [p.date for p in curve.points] == [FRI, TUE, WED]
    assert [p.twr_index for p in curve.points] == [100.0, 100.0, 100.0]
    assert [p.net_transfer for p in curve.points] == [0, 1_000_000, 0]
    # a window that starts after the deposit still sees Tuesday's flow
    short = await service.equity(user, acct, days=1)
    assert [(p.date, p.net_transfer) for p in short.points] == [(TUE, 1_000_000), (WED, 0)]


async def test_a_missing_row_with_a_gain_chains_across_the_gap() -> None:
    (repo, clock, service, jobs, user, acct), e_fri = await friday_gain(friday_row=False)
    # Friday's row is missing; Monday 10:00 deposit, Monday's row written; flat Monday
    clock.now = at(MON, 10, 0)
    await service.transfer(user, acct, xfer("in", 500_000))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 120, at(MON, 13, 29), reference=120)
    await jobs.tick(repo, at(MON, 14, 50))
    clock.now = at(TUE, 8, 0)
    summary = await service.get_account(user, acct)
    # Thursday 1,000,000 -> Monday (Friday's gain inside): (E_fri + 500,000) / (1,000,000 + 500,000)
    assert summary.total_return_pct == pct((e_fri + 500_000) / 1_500_000 - 1)


# --- 2. after the close ------------------------------------------------------------------


@pytest.mark.parametrize("friday_row", [True, False], ids=["row", "no-row"])
async def test_a_weekend_deposit_neither_earns_nor_dilutes_fridays_move(friday_row) -> None:
    (repo, clock, service, jobs, user, acct), e_fri = await friday_gain(friday_row=friday_row)
    r_fri = pct(e_fri / 1_000_000 - 1)
    before = await service.get_account(user, acct)
    assert before.total_return_pct == before.day_return_pct == r_fri
    clock.now = at(SAT, 11, 0)
    await service.transfer(user, acct, xfer("in", 1_000_000))
    for moment in (at(SAT, 12, 0), at(SUN, 20, 0), at(MON, 8, 59)):  # weekend, pre-open
        clock.now = moment
        summary = await service.get_account(user, acct)
        assert summary.equity == e_fri + 1_000_000
        assert summary.total_return_pct == summary.day_return_pct == r_fri  # not r_fri / 2
        assert summary.day_pnl == before.day_pnl == e_fri - 1_000_000
        assert (summary.day_net_transfer, summary.post_close_transfer) == (0, 1_000_000)
        assert summary.total_pnl == e_fri - 1_000_000
        curve = await service.equity(user, acct)
        live = curve.points[-1]
        assert (live.date, live.live, live.equity) == (FRI, True, e_fri + 1_000_000)
        assert (live.net_transfer, live.post_close_transfer) == (0, 1_000_000)
        assert live.twr_index == round(e_fri / 1_000_000 * 100, 4)
    if not friday_row:
        return
    clock.now = at(MON, 9, 1)  # the deposit is now at the start of Monday: flat market
    monday = await service.get_account(user, acct)
    assert monday.total_return_pct == r_fri and (monday.day_pnl, monday.day_return_pct) == (0, 0)
    assert (monday.day_net_transfer, monday.post_close_transfer) == (1_000_000, 0)


async def test_a_large_post_close_withdrawal_does_not_drop_the_days_gain() -> None:
    (repo, clock, service, jobs, user, acct), e_fri = await friday_gain(lots=9, close=110)
    r_fri = pct(e_fri / 1_000_000 - 1)
    clock.now = at(FRI, 15, 0)
    cash = (await service.get_account(user, acct)).available_cash
    await service.transfer(user, acct, xfer("out", cash))  # everything but the shares
    clock.now = at(FRI, 20, 0)
    summary = await service.get_account(user, acct)
    assert summary.post_close_transfer == -cash
    assert (summary.total_return_pct, summary.day_return_pct) == (r_fri, r_fri)
    assert summary.day_pnl == e_fri - 1_000_000
    curve = await service.equity(user, acct)
    assert curve.points[-1].twr_index == round(e_fri / 1_000_000 * 100, 4)


async def test_the_morning_after_an_evening_deposit_is_flat_on_a_flat_day() -> None:
    (repo, clock, service, jobs, user, acct), e_fri = await friday_gain(close=100)
    friday_pnl = e_fri - 1_000_000  # the fees only
    clock.now = at(FRI, 20, 0)
    await service.transfer(user, acct, xfer("in", 300_000))
    clock.now = at(MON, 8, 0)
    summary = await service.get_account(user, acct)
    assert summary.day_pnl == friday_pnl  # Friday is still "the day" before 09:00
    assert summary.day_return_pct == pct(friday_pnl / 1_000_000)
    clock.now = at(MON, 9, 30)  # flat Monday: nothing earned on the deposit
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(MON, 9, 29))
    summary = await service.get_account(user, acct)
    assert (summary.day_pnl, summary.day_return_pct) == (0, 0.0)
    assert summary.total_return_pct == pct(e_fri / 1_000_000 - 1)


async def test_a_reconcile_fill_booked_after_the_close_counts_for_the_day() -> None:
    """A fill with an in-session ``filled_at`` booked at 14:50 moves equity after 13:30; it is
    part of the day's close equity, and an evening deposit is still kept out of the day."""
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    await service.place_order(
        user, acct, OrderRequest(symbol="1101.TW", side="buy", order_type="market", lots=5)
    )
    clock.now = at(FRI, 14, 0)
    await service.transfer(user, acct, xfer("in", 200_000))  # after the close
    # the matcher missed the session; the reconcile pass books the fill at 14:50
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 10, 0, 5))
    await jobs.tick(repo, at(FRI, 10, 0, 6))  # fills with an in-session time
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 110, at(FRI, 13, 29))
    clock.now = at(FRI, 20, 0)
    summary = await service.get_account(user, acct)
    close_equity = summary.equity - 200_000
    assert summary.post_close_transfer == 200_000 and summary.day_net_transfer == 0
    assert summary.day_pnl == close_equity - 1_000_000
    assert summary.total_return_pct == pct(close_equity / 1_000_000 - 1)


# --- edge days ------------------------------------------------------------------------------


async def test_an_account_opened_after_the_close_and_on_the_weekend() -> None:
    for opened in (at(FRI, 15, 0), at(SAT, 10, 0)):
        repo, clock, service, jobs, user, acct = await setup(opened)
        await service.transfer(user, acct, xfer("in", 500_000))
        summary = await service.get_account(user, acct)
        assert (summary.total_return_pct, summary.day_pnl, summary.day_return_pct) == (0, 0, 0)
        assert summary.post_close_transfer == 500_000 and summary.day_net_transfer == 0
        curve = await service.equity(user, acct)
        assert all(p.twr_index == 100.0 for p in curve.points)
        clock.now = at(MON, 9, 30)
        monday = await service.get_account(user, acct)
        assert (monday.day_net_transfer, monday.post_close_transfer) == (500_000, 0)
        assert (monday.total_return_pct, monday.day_pnl) == (0.0, 0)


async def test_before_the_first_snapshot_of_an_epoch() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 8, 0))
    await service.transfer(user, acct, xfer("in", 1_000_000))  # pre-open: Friday's window
    clock.now = at(FRI, 11, 0)
    summary = await service.get_account(user, acct)
    assert (summary.day_net_transfer, summary.post_close_transfer) == (1_000_000, 0)
    assert (summary.total_return_pct, summary.day_pnl) == (0.0, 0)


@pytest.mark.parametrize("new_cash", [None, 3_000_000])
@pytest.mark.parametrize("when", [(11, 0), (15, 0)], ids=["in-session", "after-close"])
async def test_reset_day_with_and_without_a_new_amount(new_cash, when) -> None:
    (repo, clock, service, jobs, user, acct), e_fri = await friday_gain()
    clock.now = at(FRI, *when)
    body = ResetRequest(initial_cash=new_cash) if new_cash else None
    fresh = await service.reset_account(user, acct, body)
    start = new_cash or 1_000_000
    assert (fresh.total_return_pct, fresh.day_pnl, fresh.contributed_capital) == (0, 0, start)
    await service.transfer(user, acct, xfer("in", 100_000))
    for moment in (at(FRI, 20, 0), at(SAT, 12, 0)):
        clock.now = moment
        summary = await service.get_account(user, acct)
        assert (summary.total_return_pct, summary.day_pnl, summary.day_return_pct) == (0, 0, 0)
        assert summary.contributed_capital == start + 100_000
    await jobs.tick(repo, at(MON, 14, 50))
    clock.now = at(TUE, 9, 30)
    summary = await service.get_account(user, acct)
    assert summary.total_return_pct == 0.0 and summary.equity == start + 100_000


async def test_a_long_weekend_holiday_window() -> None:
    # Thu 2026-12-31 closes; Fri 2027-01-01 is a holiday; the next session is Mon 2027-01-04.
    thu, holiday, sat, mon = (
        date(2026, 12, 31),
        date(2027, 1, 1),
        date(2027, 1, 2),
        date(2027, 1, 4),
    )
    repo, clock, service, jobs, user, acct = await setup(at(thu, 10, 0))
    await jobs.tick(repo, at(thu, 14, 50))
    clock.now = at(holiday, 10)
    await service.transfer(user, acct, xfer("in", 10_000))
    clock.now = at(holiday, 12, 0)
    held = await service.get_account(user, acct)
    assert (held.day_net_transfer, held.post_close_transfer) == (0, 10_000)
    for moment, amount in ((at(sat, 9), 20_000), (at(mon, 8), 30_000)):
        clock.now = moment
        await service.transfer(user, acct, xfer("in", amount))
    clock.now = at(mon, 8, 30)
    pre = await service.get_account(user, acct)
    assert (pre.day_net_transfer, pre.post_close_transfer) == (0, 60_000)
    assert (pre.total_return_pct, pre.day_pnl) == (0.0, 0)
    clock.now = at(mon, 9, 30)
    live = await service.get_account(user, acct)
    assert (live.day_net_transfer, live.post_close_transfer) == (60_000, 0)
    assert (live.total_return_pct, live.day_pnl) == (0.0, 0)
    curve = await service.equity(user, acct)
    assert [(p.date, p.net_transfer, p.twr_index) for p in curve.points] == [
        (thu, 0, 100.0),
        (mon, 60_000, 100.0),
    ]


# --- 7. no transfers: exactly the pre-feature numbers ------------------------------------


async def test_without_transfers_the_index_is_equity_over_first_equity() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(THU, 10, 0))
    repo.market_observations["TW"] = [taiex(THU, 27_000.0), taiex(FRI, 27_270.0)]
    await jobs.tick(repo, at(THU, 14, 50))
    clock.now = at(FRI, 10, 0)
    await service.place_order(
        user, acct, OrderRequest(symbol="1101.TW", side="buy", order_type="market", lots=3)
    )
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 10, 0, 5))
    await jobs.tick(repo, at(FRI, 10, 0, 6))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 104, at(FRI, 13, 29))
    await jobs.tick(repo, at(FRI, 14, 50))
    assert await repo.apply_paper_corporate_action(
        make_action(MON, cash=3_300, entitled=3000, account=acct)
    )
    clock.now = at(MON, 10, 0)
    await service.place_order(
        user,
        acct,
        OrderRequest(symbol="1101.TW", side="sell", order_type="market", lots=1),
    )
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 97, at(MON, 10, 0, 5), reference=104)
    await jobs.tick(repo, at(MON, 10, 0, 6))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 98.5, at(MON, 13, 29), reference=104)
    await jobs.tick(repo, at(MON, 14, 50))
    clock.now = at(TUE, 11, 0)
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 101, at(TUE, 10, 59), reference=98.5)
    assert await repo.list_paper_transfer_entries(acct, epoch=0) == []

    curve = await service.equity(user, acct)
    assert [p.date for p in curve.points] == [THU, FRI, MON, TUE]
    e0 = curve.points[0].equity
    assert len({p.equity for p in curve.points}) == 4  # a real path, not flat
    for point in curve.points:
        assert point.twr_index == round(point.equity / e0 * 100, 4)
        assert (point.net_transfer, point.post_close_transfer) == (0, 0)

    summary = await service.get_account(user, acct)
    monday = (await repo.list_paper_snapshots(acct, epoch=0))[-1]
    assert monday.trading_date == MON
    # the pre-feature formulas
    assert summary.total_pnl == summary.equity - 1_000_000
    assert summary.total_return_pct == round(summary.total_pnl / 1_000_000 * 100, 2)
    assert summary.day_pnl == summary.equity - monday.equity
    assert summary.day_return_pct == round(summary.day_pnl / monday.equity * 100, 2)
    assert summary.dividend_income == 3_300
    assert (summary.day_net_transfer, summary.post_close_transfer) == (0, 0)


# --- server/client parity of live re-pricing ---------------------------------------------


async def _phase(ctx, moment, price_before, price_after):
    repo, clock, service, jobs, user, acct = ctx
    clock.now = moment
    repo.quotes_latest["1101.TW"] = quote("1101.TW", price_before, moment, reference=120)
    before = await service.get_account(user, acct)
    repo.quotes_latest["1101.TW"] = quote("1101.TW", price_after, moment, reference=120)
    after = await service.get_account(user, acct)
    keep = (
        "equity",
        "total_pnl",
        "day_pnl",
        "total_return_pct",
        "day_return_pct",
        "day_net_transfer",
        "post_close_transfer",
    )
    return {
        "detail": before.model_dump(mode="json", exclude={"id"}),
        "price": price_after,
        "expected": {k: getattr(after, k) for k in keep},
    }


async def parity_cases() -> dict:
    ctx, _ = await friday_gain()
    repo, clock, service, jobs, user, acct = ctx
    cases = {}
    clock.now = at(SAT, 10, 0)
    await service.transfer(user, acct, xfer("in", 400_000))
    cases["weekend"] = await _phase(ctx, at(SAT, 12, 0), 120, 126)
    clock.now = at(MON, 8, 0)
    await service.transfer(user, acct, xfer("out", 50_000))
    cases["pre_open"] = await _phase(ctx, at(MON, 8, 30), 120, 114)
    cases["in_session"] = await _phase(ctx, at(MON, 11, 0), 123, 129)
    clock.now = at(MON, 14, 0)
    await service.transfer(user, acct, xfer("in", 700_000))
    cases["after_close"] = await _phase(ctx, at(MON, 15, 0), 125, 131)
    return cases


async def test_live_repricing_parity_fixture_is_current() -> None:
    cases = await parity_cases()
    for name in ("weekend", "pre_open", "after_close"):
        assert cases[name]["detail"]["post_close_transfer"] != 0, name
    assert cases["in_session"]["detail"]["day_net_transfer"] == 350_000
    assert cases["in_session"]["detail"]["post_close_transfer"] == 0
    text = json.dumps(cases, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_PAPER_PARITY"):
        PARITY.write_text(text, encoding="utf-8")
    assert json.loads(PARITY.read_text(encoding="utf-8")) == json.loads(text), (
        "re-run with UPDATE_PAPER_PARITY=1 and check paper-math.test.ts"
    )
