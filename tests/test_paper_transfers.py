"""Deposits and withdrawals on paper accounts (入金／出金): rules, P&L identities,
time-weighted returns and snapshots. In-memory repository; no network."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from paper_support import FRI, MON, THU, Clock, at, market_repo, new_user, quote, taiex
from pydantic import ValidationError
from test_paper_dividends import make_action, make_fill

from quant_signal.application.paper_jobs import PaperJobs, TickStats, ledger_entries
from quant_signal.application.paper_trading import (
    PaperConflict,
    PaperService,
    paper_rules,
)
from quant_signal.application.paper_transfers import (
    contributed_capital,
    transfer_flow,
    twr_factors,
    twr_index,
    twr_return_pct,
)
from quant_signal.domain.paper import (
    AccountCreate,
    LedgerEntry,
    OrderRequest,
    ResetRequest,
    TransferRequest,
)

D = Decimal
SAT = date(2026, 11, 28)


def xfer(direction="in", amount=100_000, note=None, request_id=None) -> TransferRequest:
    return TransferRequest(
        direction=direction, amount=amount, note=note, request_id=request_id or uuid4()
    )


async def setup(now, cash=1_000_000):
    repo = await market_repo()
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    clock = Clock(now)
    service = PaperService(repo, clock=clock)
    user = new_user()
    summary = await service.create_account(user, AccountCreate(name="主帳戶", initial_cash=cash))
    return repo, clock, service, PaperJobs(), user, summary.id


def buy(lots=1, limit="101") -> OrderRequest:
    return OrderRequest(
        symbol="1101.TW", side="buy", order_type="limit", limit_price=D(limit), lots=lots
    )


# --- request validation -------------------------------------------------------------


@pytest.mark.parametrize("amount", [0, 999, 100_000_001, -5_000])
def test_amount_out_of_range_is_refused(amount) -> None:
    with pytest.raises(ValidationError):
        xfer(amount=amount)


def test_amount_bounds_are_inclusive_and_the_note_is_capped() -> None:
    assert xfer(amount=1_000).amount == 1_000 and xfer(amount=100_000_000).amount == 100_000_000
    assert xfer(note="x" * 60).note == "x" * 60
    assert xfer(note="  ").note is None
    with pytest.raises(ValidationError):
        xfer(note="x" * 61)
    with pytest.raises(ValidationError):
        TransferRequest.model_validate(
            {"direction": "sideways", "amount": 5_000, "request_id": str(uuid4())}
        )
    with pytest.raises(ValidationError):
        TransferRequest.model_validate({"direction": "in", "amount": 5_000})  # no request_id
    with pytest.raises(ValidationError):
        ResetRequest(initial_cash=99_999)
    assert ResetRequest().initial_cash is None and ResetRequest(initial_cash=100_000)


# --- booking and the money identities -----------------------------------------------


async def test_deposit_and_withdrawal_move_cash_but_not_pnl() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    summary = await service.transfer(user, acct, xfer("in", 250_000, "加碼"))
    assert (summary.cash, summary.available_cash, summary.equity) == (1_250_000,) * 3
    assert summary.initial_cash == 1_000_000 and summary.contributed_capital == 1_250_000
    assert (summary.total_pnl, summary.total_return_pct) == (0, 0.0)
    assert (summary.day_pnl, summary.day_return_pct) == (0, 0.0)
    summary = await service.transfer(user, acct, xfer("out", 400_000))
    assert summary.cash == 850_000 and summary.contributed_capital == 850_000
    assert (summary.total_pnl, summary.total_return_pct, summary.day_pnl) == (0, 0.0, 0)
    ledger = await repo.list_paper_ledger(acct, 0)
    assert [(e.kind, e.amount) for e in ledger] == [
        ("deposit", 1_000_000),
        ("transfer_in", 250_000),
        ("transfer_out", -400_000),
    ]
    assert ledger[1].note == "加碼" and ledger[1].request_id is not None


async def test_total_and_day_pnl_after_a_trade_and_transfers() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    await service.place_order(
        user, acct, OrderRequest(symbol="1101.TW", side="buy", order_type="market", lots=2)
    )
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 10, 0, 5))
    await jobs.tick(repo, at(FRI, 10, 0, 6))  # 200,000 shares x 100.5 = 201,000 + 286 fee
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 105, at(FRI, 10, 1))
    before = await service.get_account(user, acct)
    assert before.equity == 1_000_000 - 201_286 + 210_000 and before.total_pnl == 8_714
    after = await service.transfer(user, acct, xfer("in", 500_000))
    assert after.equity == before.equity + 500_000
    assert after.total_pnl == before.total_pnl and after.day_pnl == before.day_pnl == 8_714
    assert after.contributed_capital == 1_500_000
    out = await service.transfer(user, acct, xfer("out", 300_000))
    assert out.total_pnl == before.total_pnl and out.day_pnl == 8_714
    # the plain figures are untouched when nothing was transferred
    assert before.total_return_pct == round(8_714 / 1_000_000 * 100, 2) == 0.87


async def test_a_withdrawal_cannot_exceed_available_cash() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    order = await service.place_order(user, acct, buy(lots=2, limit="101"))
    assert order.reserved_cash == 202_287  # 202,000 + floor(287.85)
    summary = await service.get_account(user, acct)
    assert summary.cash == 1_000_000 and summary.available_cash == 1_000_000 - 202_287
    with pytest.raises(PaperConflict, match="可用資金 NT\\$797,713"):
        await service.transfer(user, acct, xfer("out", 797_714))
    after = await service.transfer(user, acct, xfer("out", 797_713))
    assert after.cash == 202_287 and after.available_cash == 0
    with pytest.raises(PaperConflict):
        await service.transfer(user, acct, xfer("out", 1_000))
    # cancelling the order frees the reserve again
    await service.cancel_order(user, acct, order.id)
    assert (await service.transfer(user, acct, xfer("out", 202_287))).cash == 0


async def test_a_deposit_may_not_take_cash_above_one_billion() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 10, 0), cash=100_000_000)
    for _ in range(9):
        await service.transfer(user, acct, xfer("in", 100_000_000))
    assert (await service.get_account(user, acct)).cash == 1_000_000_000
    with pytest.raises(PaperConflict, match="上限"):
        await service.transfer(user, acct, xfer("in", 1_000))
    assert (await service.transfer(user, acct, xfer("out", 1_000))).cash == 999_999_000


async def test_at_most_twenty_transfers_per_taipei_day() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 0, 0, 1))
    for i in range(20):
        clock.now = at(FRI, 0, 0, 1 + i)
        await service.transfer(user, acct, xfer("in", 1_000))
    clock.now = at(FRI, 23, 59, 59)
    with pytest.raises(PaperConflict, match="最多 20 筆"):
        await service.transfer(user, acct, xfer("out", 1_000))
    assert len(await repo.list_paper_transfer_entries(acct, epoch=0)) == 20
    clock.now = at(MON, 0, 0, 0)  # the next Taipei calendar day starts at midnight, not 08:00
    assert (await service.transfer(user, acct, xfer("out", 1_000))).cash == 1_000_000 + 19_000
    # another account has its own count
    other = await service.create_account(user, AccountCreate(name="第二戶"))
    assert (await service.transfer(user, other.id, xfer("in", 1_000))).cash == 1_001_000


async def test_the_same_request_id_returns_the_original_result_and_books_once() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    body = xfer("in", 300_000)
    first = await service.transfer(user, acct, body)
    again = await service.transfer(user, acct, body)
    assert first.cash == again.cash == 1_300_000
    # even a different payload under the same id is answered with the original posting
    clock.now = at(FRI, 11, 0)
    third = await service.transfer(user, acct, xfer("out", 900_000, request_id=body.request_id))
    assert third.cash == 1_300_000
    assert len([e for e in await repo.list_paper_ledger(acct, 0) if e.kind == "transfer_in"]) == 1
    listing = await service.list_transfers(user, acct)
    assert [(t.direction, t.amount) for t in listing.transfers] == [("in", 300_000)]


async def test_a_repeat_after_a_reset_still_returns_the_original() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    body = xfer("in", 300_000)
    await service.transfer(user, acct, body)
    clock.now = at(FRI, 11, 0)
    await service.reset_account(user, acct)
    after = await service.transfer(user, acct, body)  # a late double click
    assert after.cash == 1_000_000 and after.contributed_capital == 1_000_000


async def test_listing_is_newest_first_paged_and_scoped_to_the_epoch() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    for i in range(5):
        clock.now = at(FRI, 10, i)
        await service.transfer(
            user, acct, xfer("in" if i % 2 == 0 else "out", 1_000 * (i + 1), note=f"n{i}")
        )
    page = await service.list_transfers(user, acct, limit=2, offset=0)
    assert [t.note for t in page.transfers] == ["n4", "n3"] and page.has_more
    last = await service.list_transfers(user, acct, limit=2, offset=4)
    assert [t.note for t in last.transfers] == ["n0"] and not last.has_more
    clock.now = at(FRI, 12, 0)
    await service.reset_account(user, acct)
    assert (await service.list_transfers(user, acct)).transfers == []
    assert len((await service.list_transfers(user, acct, include_archived=True)).transfers) == 5


# --- reset with a new amount ---------------------------------------------------------


async def test_reset_with_and_without_a_new_amount() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    await service.transfer(user, acct, xfer("in", 400_000))
    clock.now = at(FRI, 11, 0)
    same = await service.reset_account(user, acct)
    assert (same.initial_cash, same.cash, same.contributed_capital) == (1_000_000,) * 3
    same = await service.reset_account(user, acct, ResetRequest())
    assert same.initial_cash == 1_000_000
    bigger = await service.reset_account(user, acct, ResetRequest(initial_cash=5_000_000))
    assert (bigger.initial_cash, bigger.cash, bigger.contributed_capital) == (5_000_000,) * 3
    assert (bigger.total_pnl, bigger.total_return_pct) == (0, 0.0)
    # the new amount sticks for the next plain reset
    assert (await service.reset_account(user, acct)).initial_cash == 5_000_000


# --- time-weighted return ------------------------------------------------------------


def test_without_transfers_the_chain_equals_the_plain_return() -> None:
    steps = [(1_020_000, 0), (990_000, 0), (1_137_000, 0)]
    assert twr_return_pct(1_000_000, steps) == round((1_137_000 / 1_000_000 - 1) * 100, 2) == 13.7
    assert twr_return_pct(1_000_000, []) == 0.0
    assert twr_index([(100, 0), (110, 0), (99, 0)]) == [100.0, 110.0, 99.0]


def test_a_deposit_that_doubles_a_flat_account_is_zero_percent() -> None:
    assert twr_return_pct(1_000_000, [(2_000_000, 1_000_000)]) == 0.0
    assert twr_return_pct(1_000_000, [(500_000, -500_000)]) == 0.0  # and so is a halving withdrawal
    assert twr_index([(1_000_000, 0), (2_000_000, 1_000_000)]) == [100.0, 100.0]


def test_degenerate_days_cannot_blow_up_the_chain() -> None:
    assert twr_factors(0, [(0, 0), (500_000, 500_000)]) == [
        D(1),
        D(1),
    ]  # nothing to measure against
    assert twr_factors(100, [(0, 100)]) == [D(0)]  # lost the deposit too: floored at zero


def test_worked_multi_day_example_by_hand() -> None:
    # Start 1,000,000.
    # Day 1: the market gives +10%                        E1 = 1,100,000,  F1 = 0
    #        factor = (1,100,000 - 0) / 1,000,000 = 1.10
    # Day 2: deposit 1,100,000, then the original money earns +5%
    #        E2 = 1,100,000 x 1.05 + 1,100,000 = 2,255,000, F2 = 1,100,000
    #        factor = (2,255,000 - 1,100,000) / 1,100,000 = 1.05
    # Day 3: withdraw 500,000, the rest loses 10%
    #        E3 = 2,255,000 x 0.90 - 500,000 = 1,529,500,  F3 = -500,000
    #        factor = (1,529,500 + 500,000) / 2,255,000 = 0.90
    # Time-weighted: 1.10 x 1.05 x 0.90 = 1.0395 -> +3.95%
    # The naive figure would be (1,529,500 - 1,000,000) / 1,000,000 = +52.95% (deposit counted
    # as profit); against contributed capital 1,000,000 + 1,100,000 - 500,000 = 1,600,000 the
    # profit is -70,500 (-4.41%), which is money-weighted and what total_pnl reports.
    steps = [(1_100_000, 0), (2_255_000, 1_100_000), (1_529_500, -500_000)]
    assert twr_factors(1_000_000, steps) == [D("1.1"), D("1.05"), D("0.9")]
    assert twr_return_pct(1_000_000, steps) == 3.95
    assert twr_index([(1_000_000, 0), *steps]) == [100.0, 110.0, 115.5, 103.95]
    assert 1_529_500 - (1_000_000 + 1_100_000 - 500_000) == -70_500


def _entry(kind, amount, when) -> LedgerEntry:
    return LedgerEntry(account_id=uuid4(), at=when, kind=kind, amount=amount, request_id=uuid4())


def test_flow_windows_use_taipei_dates_and_ignore_other_kinds() -> None:
    entries = [
        _entry("transfer_in", 100, at(THU, 23, 59, 59)),
        _entry("transfer_out", -30, at(FRI, 0, 0, 0)),
        _entry("transfer_in", 7, at(SAT, 12)),
        LedgerEntry(account_id=uuid4(), at=at(FRI, 10), kind="deposit", amount=999),
        LedgerEntry(account_id=uuid4(), at=at(FRI, 10), kind="reset", amount=999),
    ]
    assert transfer_flow(entries) == 77
    assert transfer_flow(entries, after=THU, through=FRI) == -30
    assert transfer_flow(entries, after=FRI) == 7
    assert contributed_capital(1_000, entries) == 1_077


async def test_summary_returns_are_time_weighted_over_several_days() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 9, 30))
    repo.market_observations["TW"] = [taiex(FRI, 27_000.0), taiex(MON, 27_000.0)]
    # Buy 1 lot on Friday; mark Friday at 110 -> the day's gain; deposit on Monday, mark 99.
    await service.place_order(
        user, acct, OrderRequest(symbol="1101.TW", side="buy", order_type="market", lots=1)
    )
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59, 59))
    await jobs.tick(repo, at(FRI, 10, 0, 1))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 110, at(FRI, 13, 30))
    await jobs.tick(repo, at(FRI, 14, 50))
    friday = (await repo.list_paper_snapshots(acct, epoch=0))[0]
    assert friday.net_transfer == 0
    # Monday: deposit 2,000,000 (a big, badly timed one), the position drops to 99
    clock.now = at(MON, 10, 0)
    await service.transfer(user, acct, xfer("in", 2_000_000))
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 99, at(MON, 10, 1), reference=110)
    summary = await service.get_account(user, acct)
    cash = 1_000_000 - 100_500 - 143 + 2_000_000
    assert summary.equity == cash + 99_000 and summary.contributed_capital == 3_000_000
    # day_pnl = E - Friday close - Monday's transfer = (99,000 - 110,000) = -11,000
    assert summary.day_pnl == -11_000
    assert summary.day_return_pct == round(-11_000 / friday.equity * 100, 2)
    # time-weighted: Friday factor x Monday factor
    f1 = friday.equity / 1_000_000
    f2 = (summary.equity - 2_000_000) / friday.equity
    assert summary.total_return_pct == round((f1 * f2 - 1) * 100, 2)
    assert summary.total_pnl == summary.equity - 3_000_000
    # the series carries the flow and the index
    curve = await service.equity(user, acct, days=30)
    assert [p.net_transfer for p in curve.points] == [0, 2_000_000] and curve.points[-1].live
    assert curve.points[0].twr_index == 100.0
    assert curve.points[1].twr_index == pytest.approx(100 * f2, abs=1e-3)


async def test_dividend_cash_stays_profit_not_a_transfer() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 10, 0))
    fill = make_fill("buy", 1000, "100", at(FRI, 10), account=acct)
    order_id = fill.order_id
    from quant_signal.domain.paper import PaperOrderRecord

    await repo.add_paper_order(
        PaperOrderRecord(
            id=order_id,
            account_id=acct,
            symbol="1101.TW",
            name="台泥",
            side="buy",
            order_type="market",
            lots=1,
            session_date=FRI,
            created_at=at(FRI, 10),
            updated_at=at(FRI, 10),
            reserved_cash=100_500,
        )
    )
    assert await repo.apply_paper_fill(fill, ledger_entries(fill, at(FRI, 10)), now=at(FRI, 10))
    assert await repo.apply_paper_corporate_action(
        make_action(MON, cash=2_500, entitled=1000, account=acct)
    )
    clock.now = at(MON, 9, 30)
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(MON, 9, 29))
    summary = await service.get_account(user, acct)
    assert summary.dividend_income == 2_500 and summary.contributed_capital == 1_000_000
    assert summary.total_pnl == summary.equity - 1_000_000
    assert summary.total_pnl == summary.realized_pnl + summary.unrealized_pnl + 2_500
    assert await repo.list_paper_transfer_entries(acct, epoch=0) == []
    assert summary.total_return_pct == round(summary.total_pnl / 1_000_000 * 100, 2)  # plain path


# --- snapshots ------------------------------------------------------------------------


async def test_snapshot_net_transfer_late_evening_weekend_and_rewrites() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 9, 30))
    await service.transfer(user, acct, xfer("in", 200_000))  # Friday morning
    await jobs.tick(repo, at(FRI, 14, 50))
    snaps = {s.trading_date: s for s in await repo.list_paper_snapshots(acct, epoch=0)}
    assert (snaps[FRI].net_transfer, snaps[FRI].cash) == (200_000, 1_200_000)

    # 20:00 on Friday, after the job wrote and finalised the day: the row is re-written.
    clock.now = at(FRI, 20, 0)
    await service.transfer(user, acct, xfer("out", 50_000))
    friday = (await repo.list_paper_snapshots(acct, epoch=0))[0]
    assert (friday.net_transfer, friday.cash, friday.equity) == (150_000, 1_150_000, 1_150_000)
    # ...and the day's flow is not counted twice in the live figures
    summary = await service.get_account(user, acct)
    assert (summary.day_pnl, summary.total_pnl) == (0, 0) and summary.total_return_pct == 0.0

    # Saturday: belongs to Monday's row, Friday's stays as it was
    clock.now = at(SAT, 11, 0)
    await service.transfer(user, acct, xfer("in", 30_000))
    assert (await repo.list_paper_snapshots(acct, epoch=0))[0].net_transfer == 150_000
    clock.now = at(MON, 10, 0)
    await service.transfer(user, acct, xfer("in", 5_000))
    await jobs.tick(repo, at(MON, 14, 50))
    snaps = {s.trading_date: s for s in await repo.list_paper_snapshots(acct, epoch=0)}
    assert snaps[MON].net_transfer == 35_000 and snaps[MON].cash == 1_185_000
    assert snaps[FRI].net_transfer == 150_000

    # A past day that is re-valued keeps its own window (and so does the later one)
    await jobs._rewrite_snapshots(repo, {FRI: {acct}}, at(MON, 15, 0), TickStats())
    snaps = {s.trading_date: s for s in await repo.list_paper_snapshots(acct, epoch=0)}
    assert (snaps[FRI].net_transfer, snaps[MON].net_transfer) == (150_000, 35_000)
    assert snaps[MON].equity == 1_185_000

    # The chain over the stored rows is flat: no transfer ever shows up as a return
    steps = [(s.equity, s.net_transfer) for s in snaps.values()]
    assert twr_return_pct(1_000_000, steps) == 0.0


async def test_a_transfer_before_the_snapshot_time_waits_for_the_job() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 9, 30))
    await service.transfer(user, acct, xfer("in", 10_000))
    assert await repo.list_paper_snapshots(acct, epoch=0) == []  # nothing written by the request
    await jobs.tick(repo, at(FRI, 14, 50))
    assert (await repo.list_paper_snapshots(acct, epoch=0))[0].net_transfer == 10_000


async def test_a_reset_starts_the_flow_over() -> None:
    repo, clock, service, jobs, user, acct = await setup(at(FRI, 9, 30))
    await service.transfer(user, acct, xfer("in", 200_000))
    await jobs.tick(repo, at(FRI, 14, 50))
    clock.now = at(FRI, 15, 0)
    await service.reset_account(user, acct, ResetRequest(initial_cash=2_000_000))
    summary = await service.get_account(user, acct)
    assert summary.contributed_capital == 2_000_000 and summary.cash == 2_000_000
    assert await repo.list_paper_transfer_entries(acct, epoch=1) == []
    await jobs.tick(repo, at(FRI, 15, 20))
    new = await repo.list_paper_snapshots(acct, epoch=1)
    assert [(s.net_transfer, s.equity) for s in new] == [(0, 2_000_000)]


def test_rules_text_mentions_transfers_and_time_weighting() -> None:
    notes = " ".join(paper_rules(1, 3)["notes"])
    assert "入金" in notes and "時間加權" in notes
