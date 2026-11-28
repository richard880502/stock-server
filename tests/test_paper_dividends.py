"""Dividends in paper trading: the book, the job, snapshots and the REST route (no network)."""

from __future__ import annotations

import ast
import pathlib
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from paper_support import FRI, MON, THU, TUE, Clock, at, market_repo, new_user, quote, stock_row

from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.adapters.rest.security import Principal, require_principal
from quant_signal.application import paper_trading
from quant_signal.application.paper_actions import (
    apply_due_corporate_actions,
    cutoff_date,
    load_book,
)
from quant_signal.application.paper_jobs import PaperJobs, ledger_entries
from quant_signal.application.paper_trading import PaperService
from quant_signal.domain.models import User
from quant_signal.domain.paper import AccountCreate, PaperFillRecord, PaperOrderRecord
from quant_signal.domain.paper_actions import CorporateActionRecord
from quant_signal.domain.short_data import DividendEvent
from quant_signal.quant.paper_book import replay
from quant_signal.quant.paper_dividends import entitled_shares, ex_at, payout
from quant_signal.quant.paper_matching import compute_fill

D = Decimal
WED = date(2026, 12, 2)
SYMBOL = "1101.TW"


# --- builders ----------------------------------------------------------------------


def make_fill(side, shares, price, when, symbol=SYMBOL, account=None, epoch=0) -> PaperFillRecord:
    parts = compute_fill(side, shares, D(price), symbol=symbol)
    return PaperFillRecord(
        order_id=uuid4(),
        account_id=account or uuid4(),
        epoch=epoch,
        symbol=symbol,
        side=side,
        shares=shares,
        price=D(price),
        amount=parts.amount,
        commission=parts.commission,
        tax=parts.tax,
        filled_at=when,
        price_source="tick",
        price_time=when,
    )


def make_action(
    ex_date, *, cash=0, added=0, entitled=1000, symbol=SYMBOL, account=None, epoch=0
) -> CorporateActionRecord:
    return CorporateActionRecord(
        account_id=account or uuid4(),
        epoch=epoch,
        symbol=symbol,
        ex_date=ex_date,
        event_kind="ex_rights_dividend",
        entitled_shares=entitled,
        cash_amount=cash,
        shares_added=added,
        ex_at=ex_at(ex_date),
        applied_at=at(ex_date, 8, 5),
        source="test",
        source_available_at=at(THU, 8),
    )


def event(
    ex_date, *, cash=None, stock=None, symbol=SYMBOL, available=None, revision=1, withdrawn=False
):
    return DividendEvent(
        symbol=symbol,
        ex_date=ex_date,
        kind="ex_rights_dividend" if stock else "ex_dividend",
        source="test",
        cash_dividend_per_share=cash,
        stock_dividend_per_share=stock,
        withdrawn=withdrawn,
        available_at=available or at(THU, 8),
        revision=revision,
    )


# --- the book ----------------------------------------------------------------------


def test_cash_only_stock_only_and_both() -> None:
    buy = make_fill("buy", 1000, "100", at(FRI, 10))  # cost 100,000 + 142
    cash = replay([buy], [make_action(MON, cash=2500)])
    assert (cash.held(SYMBOL), cash.dividend_income) == (1000, 2500)
    assert cash.positions[SYMBOL].cost == 100_142 and cash.positions[SYMBOL].dividends == 2500

    stock = replay([buy], [make_action(MON, added=50)])
    state = stock.positions[SYMBOL]
    assert (state.shares, state.cost, stock.dividend_income) == (1050, 100_142, 0)
    assert state.avg_cost == D(100_142) / D(1050)  # the average falls, the total does not

    both = replay([buy], [make_action(MON, cash=2500, added=50)])
    assert (both.held(SYMBOL), both.dividend_income) == (1050, 2500)


def test_payout_rounds_down_and_ignores_empty_rights() -> None:
    p = payout(1000, 2.5, 0.05)
    assert (p.cash_amount, p.shares_added) == (2500, 50)
    p = payout(1050, 0.375, 0.0333)  # 393.75 -> 393, 34.965 -> 34
    assert (p.cash_amount, p.shares_added) == (393, 34)
    assert payout(3, 0.1, 0.1) is None  # 0.3 and 0.3 round down to nothing
    assert payout(0, 5, 0.5) is None
    assert payout(1000, None, None) is None and payout(1000, 0, 0) is None
    assert payout(1000, 0.1 + 0.2, None).cash_amount == 300  # float noise cannot cost a dollar


def test_entitlement_before_on_and_after_the_ex_date() -> None:
    before = make_fill("buy", 1000, "100", at(FRI, 10))
    on_day = make_fill("buy", 2000, "100", at(MON, 9, 1))
    after = make_fill("buy", 3000, "100", at(TUE, 10))
    assert entitled_shares([before, on_day, after], [], SYMBOL, MON) == 1000
    assert entitled_shares([on_day, after], [], SYMBOL, MON) == 0
    assert entitled_shares([before, on_day, after], [], SYMBOL, TUE) == 3000  # Fri + Mon buys


def test_sold_on_the_ex_date_is_still_entitled_and_keeps_the_bonus_shares() -> None:
    buy = make_fill("buy", 1000, "100", at(FRI, 10))
    sell = make_fill("sell", 1000, "100", at(MON, 10))
    assert entitled_shares([buy, sell], [], SYMBOL, MON) == 1000
    book = replay([buy, sell], [make_action(MON, cash=2500, added=50)])
    assert book.held(SYMBOL) == 50  # the bonus shares arrive at 08:30, before the sale
    assert book.open_positions()[0].shares == 50


def test_two_events_in_sequence_the_second_sees_the_first() -> None:
    buy = make_fill("buy", 1000, "100", at(FRI, 10))
    first = payout(entitled_shares([buy], [], SYMBOL, MON), 0, 0.1)
    action1 = make_action(MON, added=first.shares_added)
    assert action1.shares_added == 100
    entitled2 = entitled_shares([buy], [action1], SYMBOL, WED)
    assert entitled2 == 1100
    assert payout(entitled2, 1.0, None).cash_amount == 1100
    # and an event BEFORE the other one does not see the later action
    assert entitled_shares([buy], [action1], SYMBOL, MON) == 1000


def test_selling_bonus_shares_realises_against_the_lower_average_cost() -> None:
    buy = make_fill("buy", 1000, "100", at(FRI, 10))  # cost 100,142
    action = make_action(MON, cash=2500, added=50)
    partial = make_fill("sell", 50, "110", at(TUE, 10))  # 5,500; fee 20 (the minimum); tax 16
    book = replay([buy, partial], [action])
    removed = round(D(100_142) * 50 / 1050 + D("0.0000001"))  # 4,768.67 -> 4,769
    assert book.realized_by_fill[partial.id] == (5_500 - 20 - 16) - removed == 695
    assert (
        book.positions[SYMBOL].shares == 1000 and book.positions[SYMBOL].cost == 100_142 - removed
    )
    everything = make_fill("sell", 1050, "110", at(TUE, 10))  # 115,500; fee 164; tax 346
    full = replay([buy, everything], [action])
    assert full.realized_total == (115_500 - 164 - 346) - 100_142 == 14_848
    assert full.open_positions() == [] and full.dividend_income == 2500


def test_cash_identity_with_dividends() -> None:
    buy = make_fill("buy", 1000, "100", at(FRI, 10))
    action = make_action(MON, cash=2500, added=50)
    book = replay([buy], [action])
    cash = 1_000_000 - 100_142 + 2500
    cost = book.positions[SYMBOL].cost
    assert cash - 1_000_000 == book.realized_total + book.dividend_income - cost


# --- every module builds its book through load_book -------------------------------------


def test_no_module_replays_fills_on_its_own() -> None:
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "quant_signal"
    allowed = {"quant/paper_book.py", "application/paper_actions.py", "quant/paper_dividends.py"}
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel in allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name == "replay":
                    offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], f"use application.paper_actions.load_book instead: {offenders}"


# --- the job -----------------------------------------------------------------------


async def seeded(*, ex_cash=2.5, ex_stock=0.05, sell_on_ex: bool = False):
    """An account holding 2 lots since Friday, a dividend event for Monday."""
    repo = await market_repo()
    clock = Clock(at(FRI, 9, 30))
    service = PaperService(repo, clock=clock)
    user = new_user()
    summary = await service.create_account(user, AccountCreate(name="主帳戶"))
    account = await repo.get_paper_account(summary.id)
    fills = [make_fill("buy", 2000, "100", at(FRI, 10), account=account.id)]
    if sell_on_ex:
        fills.append(make_fill("sell", 2000, "100", at(MON, 10), account=account.id))
    for fill in fills:
        order = PaperOrderRecord(
            id=fill.order_id,
            account_id=account.id,
            symbol=SYMBOL,
            name="台泥",
            side=fill.side,
            order_type="market",
            lots=fill.shares // 1000,
            status="filled",
            session_date=local_day(fill.filled_at),
            created_at=fill.filled_at,
            updated_at=fill.filled_at,
        )
        repo.paper.orders[order.id] = order
        repo.paper.fills[fill.id] = fill
        repo.paper.fill_by_order[order.id] = fill.id
        repo.paper.ledger.extend(ledger_entries(fill, fill.filled_at))
    if ex_cash is not None or ex_stock is not None:
        await repo.upsert_dividend_events([event(MON, cash=ex_cash, stock=ex_stock)])
    return repo, service, user, account


def local_day(moment: datetime) -> date:
    from quant_signal.quant.paper_calendar import local_date

    return local_date(moment)


async def test_dividend_is_booked_on_the_ex_date_and_equity_stays_continuous() -> None:
    repo, service, user, account = await seeded()
    # Cash 1,000,000 - 200,000 - 285 = 799,715. Two lots, ex-date Monday 2.5 cash + 0.05 stock.
    jobs = PaperJobs()
    now = at(MON, 8, 5)
    stats = await jobs.tick(repo, now)
    assert stats.dividends == 1
    ledger = [e for e in await repo.list_paper_ledger(account.id, 0) if e.kind == "dividend"]
    assert [(e.amount, e.at) for e in ledger] == [(5000, ex_at(MON))]
    assert ledger[0].action_id is not None and ledger[0].fill_id is None
    book = await load_book(repo, account.id, 0)
    assert book.held(SYMBOL) == 2100 and book.dividend_income == 5000
    assert book.positions[SYMBOL].cost == 200_285  # total cost unchanged

    service.clock = Clock(at(MON, 8, 6))
    detail = await service.get_account(user, account.id)
    assert detail.cash == 799_715 + 5000 and detail.dividend_income == 5000
    assert detail.positions[0].shares == 2100 and detail.positions[0].dividend_income == 5000
    # the figures add up: total = realised + unrealised + dividends
    assert detail.total_pnl == detail.realized_pnl + detail.unrealized_pnl + detail.dividend_income

    listed = await service.list_corporate_actions(user, account.id)
    assert listed.has_more is False and len(listed.actions) == 1
    row = listed.actions[0]
    assert (row.symbol, row.name, row.ex_date) == (SYMBOL, "台泥", MON)
    assert (row.entitled_shares, row.cash_amount, row.shares_added) == (2000, 5000, 100)


async def test_idempotent_across_ticks_and_a_restart() -> None:
    repo, _, _, account = await seeded()
    jobs = PaperJobs()
    await jobs.tick(repo, at(MON, 8, 5))
    await jobs.tick(repo, at(MON, 8, 6))
    await jobs.apply_corporate_actions(repo, at(MON, 8, 7))  # forced second pass
    fresh = PaperJobs()  # a restart: no memory of anything
    stats = await fresh.tick(repo, at(MON, 14, 0))
    assert stats.dividends == 0
    assert len(await repo.list_paper_corporate_actions(account.id, epoch=0)) == 1
    assert (
        sum(e.amount for e in await repo.list_paper_ledger(account.id, 0) if e.kind == "dividend")
        == 5000
    )


async def test_throttle_session_and_catch_up_gates() -> None:
    repo, _, _, account = await seeded()
    jobs = PaperJobs()
    assert jobs._actions_due(at(MON, 10, 0)) is False  # never during the session
    await jobs.tick(repo, at(MON, 8, 5))
    assert jobs._actions_due(at(MON, 8, 10)) is False  # throttled
    assert jobs._actions_due(at(MON, 8, 40)) is True  # 30 minutes later
    await jobs.tick(repo, at(MON, 16, 5))
    assert jobs._actions_due(at(MON, 20, 0)) is False  # settled for the day
    assert jobs._actions_due(at(TUE, 8, 0)) is True  # the next day starts afresh
    assert PaperJobs()._actions_due(at(MON, 20, 0)) is True  # restart catches up
    assert cutoff_date(at(MON, 7, 59)) == date(2026, 11, 29) and cutoff_date(at(MON, 8, 0)) == MON


async def test_nothing_before_08_00_and_nothing_for_future_ex_dates() -> None:
    repo, _, _, account = await seeded()
    assert (await apply_due_corporate_actions(repo, at(MON, 7, 30))).applied == []
    assert (await apply_due_corporate_actions(repo, at(FRI, 20, 0))).applied == []
    assert len((await apply_due_corporate_actions(repo, at(MON, 8, 0))).applied) == 1


async def test_events_not_yet_available_or_withdrawn_are_ignored() -> None:
    repo, _, _, account = await seeded(ex_cash=None, ex_stock=None)
    await repo.upsert_dividend_events([event(MON, cash=2.5, available=at(MON, 12))])
    assert (await apply_due_corporate_actions(repo, at(MON, 8, 5))).applied == []
    assert (
        len((await apply_due_corporate_actions(repo, at(MON, 14, 0))).applied) == 1
    )  # visible now

    repo, _, _, account = await seeded(ex_cash=None, ex_stock=None)
    await repo.upsert_dividend_events(
        [
            event(MON, cash=2.5),
            event(MON, cash=2.5, withdrawn=True, revision=2, available=at(THU, 9)),
        ]
    )
    assert (await apply_due_corporate_actions(repo, at(MON, 8, 5))).applied == []


async def test_sold_on_the_ex_date_still_gets_the_dividend_and_keeps_the_symbol_open() -> None:
    repo, _, _, account = await seeded(sell_on_ex=True)
    await PaperJobs().apply_corporate_actions(repo, at(MON, 8, 5))  # before the sale
    book = await load_book(repo, account.id, 0)
    assert book.held(SYMBOL) == 100  # 2,100 - 2,000 sold
    assert await repo.list_open_position_symbols() == [SYMBOL]  # fills net 0, the bonus remains
    # a catch-up run AFTER the sale (e.g. the worker was down) reaches the same entitlement
    repo2, _, _, account2 = await seeded(sell_on_ex=True)
    report = await apply_due_corporate_actions(repo2, at(TUE, 8, 5))
    assert len(report.applied) == 1
    stored = (await repo2.list_paper_corporate_actions(account2.id, epoch=0))[0]
    assert (stored.entitled_shares, stored.cash_amount, stored.shares_added) == (2000, 5000, 100)


async def test_reset_between_purchase_and_ex_date_gets_nothing() -> None:
    repo, service, user, account = await seeded()
    await service.reset_account(user, account.id)
    report = await apply_due_corporate_actions(repo, at(MON, 8, 5))
    assert report.applied == []
    assert await repo.list_paper_corporate_actions(account.id, epoch=None) == []


async def test_a_working_order_of_an_earlier_session_defers_the_entitlement() -> None:
    repo, _, _, account = await seeded()
    order = PaperOrderRecord(
        account_id=account.id,
        symbol=SYMBOL,
        name="台泥",
        side="buy",
        order_type="market",
        lots=1,
        session_date=FRI,
        created_at=at(FRI, 13, 40),
        updated_at=at(FRI, 13, 40),
    )
    repo.paper.orders[order.id] = order
    assert (await apply_due_corporate_actions(repo, at(MON, 8, 5))).applied == []
    await repo.finish_paper_order(order.id, status="expired", now=at(MON, 8, 6))
    assert len((await apply_due_corporate_actions(repo, at(MON, 8, 7))).applied) == 1


async def test_budget_bounds_a_run_and_the_rest_follows() -> None:
    repo, _, _, first = await seeded()
    service = PaperService(repo, clock=Clock(at(FRI, 9, 30)))
    second = await service.create_account(new_user(), AccountCreate(name="第二帳戶"))
    fill = make_fill("buy", 1000, "100", at(FRI, 10), account=second.id)
    order = PaperOrderRecord(
        id=fill.order_id,
        account_id=second.id,
        symbol=SYMBOL,
        name="台泥",
        side="buy",
        order_type="market",
        lots=1,
        status="filled",
        session_date=FRI,
        created_at=fill.filled_at,
        updated_at=fill.filled_at,
    )
    repo.paper.orders[order.id] = order
    repo.paper.fills[fill.id] = fill
    repo.paper.fill_by_order[order.id] = fill.id
    run1 = await apply_due_corporate_actions(repo, at(MON, 8, 5), budget=1)
    assert len(run1.applied) == 1 and run1.complete is False
    run2 = await apply_due_corporate_actions(repo, at(MON, 8, 6), budget=1)
    assert len(run2.applied) == 1
    assert {first.id, second.id} == {run1.applied[0][0], run2.applied[0][0]}
    jobs = PaperJobs()
    await jobs.apply_corporate_actions(repo, at(MON, 8, 7))
    assert jobs._actions_more is False


async def test_catch_up_of_an_old_event_rewrites_the_affected_snapshots() -> None:
    repo, service, user, account = await seeded(ex_cash=None, ex_stock=None)
    await repo.upsert_market_stock_days(
        [stock_row(SYMBOL, d, close=100.0, name="台泥") for d in (FRI, MON, TUE)]
    )
    jobs = PaperJobs()
    for day in (FRI, MON, TUE):  # snapshots as they were written before the event was known
        snap, _ = await jobs.snapshot_account(repo, account, day, at(day, 15))
        await repo.upsert_paper_snapshot(snap)
    before = {s.trading_date: s for s in await repo.list_paper_snapshots(account.id, epoch=0)}
    assert before[MON].cash == before[FRI].cash == 799_715

    await repo.upsert_dividend_events([event(MON, cash=2.5, stock=0.05, available=at(WED, 7))])
    stats = await jobs.apply_corporate_actions(repo, at(WED, 8, 5))
    assert stats.dividends == 1
    after = {s.trading_date: s for s in await repo.list_paper_snapshots(account.id, epoch=0)}
    assert after[FRI] == before[FRI]  # before the ex-date nothing changes: no fake jump
    assert after[MON].cash == 799_715 + 5000 and after[TUE].cash == 799_715 + 5000
    assert after[MON].market_value == 2100 * 100  # 2,000 + 100 bonus shares at the 100 close
    assert after[MON].equity == 799_715 + 5000 + 210_000
    # the day-over-day change into the ex-date is the dividend itself (the close did not move)
    assert after[MON].equity - after[FRI].equity == 5000 + 10_000  # cash + bonus shares' value


# --- REST ---------------------------------------------------------------------------


ME = uuid4()


class Who:
    principal = Principal(
        "user",
        user=User(id=ME, google_sub="g-me", email="me@x.com", created_at=datetime.now(UTC)),
    )


@pytest.fixture
def client(monkeypatch):
    repo_box: dict = {}

    async def override():
        yield repo_box["repo"]

    real = paper_trading.PaperService.__init__

    def pinned(self, *args, **kwargs):
        kwargs.setdefault("clock", lambda: at(MON, 10, 0))
        real(self, *args, **kwargs)

    monkeypatch.setattr(paper_trading.PaperService, "__init__", pinned)
    app.dependency_overrides[get_repository] = override
    app.dependency_overrides[require_principal] = lambda: Who.principal
    with TestClient(app) as c:
        c.repo_box = repo_box  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


async def test_rest_lists_applied_actions_and_account_fields(client) -> None:
    repo, service, user, account = await seeded()
    # Re-own the seeded account by the REST principal.
    repo.paper.accounts[account.id].user_id = ME
    repo.quotes_latest[SYMBOL] = quote(SYMBOL, 100, at(MON, 9, 59))
    client.repo_box["repo"] = repo
    await PaperJobs().apply_corporate_actions(repo, at(MON, 8, 5))
    url = f"/api/v1/paper/accounts/{account.id}"
    body = client.get(f"{url}/corporate-actions").json()
    assert body["has_more"] is False
    (row,) = body["actions"]
    assert row["symbol"] == SYMBOL and row["name"] == "台泥" and row["ex_date"] == MON.isoformat()
    assert (row["entitled_shares"], row["cash_amount"], row["shares_added"]) == (2000, 5000, 100)
    assert row["kind"] == "ex_rights_dividend" and row["cash_dividend_per_share"] == 2.5
    assert client.get(f"{url}/corporate-actions?limit=1&offset=1").json() == {
        "actions": [],
        "has_more": False,
    }
    assert client.get(f"{url}/corporate-actions?limit=0").status_code == 422
    detail = client.get(url).json()
    assert detail["dividend_income"] == 5000
    assert detail["positions"][0]["dividend_income"] == 5000
    assert detail["positions"][0]["shares"] == 2100

    Who.principal = Principal("api_key")
    assert client.get(f"{url}/corporate-actions").status_code == 403
    Who.principal = Principal(
        "user",
        user=User(id=uuid4(), google_sub="g-you", email="you@x.com", created_at=datetime.now(UTC)),
    )
    assert client.get(f"{url}/corporate-actions").status_code == 404
    Who.principal = Principal(
        "user",
        user=User(id=ME, google_sub="g-me", email="me@x.com", created_at=datetime.now(UTC)),
    )


async def test_memory_twin_rejects_duplicates_and_overlong_values() -> None:
    repo, _, _, account = await seeded()
    action = make_action(MON, cash=5000, added=100, entitled=2000, account=account.id)
    assert await repo.apply_paper_corporate_action(action) is True
    again = action.model_copy(update={"id": uuid4()})
    assert await repo.apply_paper_corporate_action(again) is False  # same account/epoch/symbol/day
    assert await repo.apply_paper_corporate_action(action) is False
    assert sum(e.amount for e in repo.paper.ledger if e.kind == "dividend") == 5000
    too_long = make_action(TUE, cash=1, account=account.id, symbol="X" * 33)
    with pytest.raises(ValueError):
        await repo.apply_paper_corporate_action(too_long)
    # an old epoch's action cannot be written after a reset
    await repo.reset_paper_account(account.id, now=at(TUE, 8))
    stale = make_action(WED, cash=1, account=account.id, epoch=0)
    assert await repo.apply_paper_corporate_action(stale) is False


# --- review fixes: settled no-payout events, re-valuation that survives a failure ----


def _count_fill_loads(repo) -> list[int]:
    calls = [0]
    original = repo.list_paper_fills

    async def counted(*args, **kwargs):
        calls[0] += 1
        return await original(*args, **kwargs)

    repo.list_paper_fills = counted
    return calls


async def test_a_no_payout_event_is_evaluated_once_per_revision() -> None:
    repo, _, _, account = await seeded(ex_cash=None, ex_stock=None, sell_on_ex=True)
    await repo.upsert_dividend_events([event(TUE, cash=1.0)])  # sold out on Monday: 0 held
    loads = _count_fill_loads(repo)
    assert (await apply_due_corporate_actions(repo, at(TUE, 8, 5))).applied == []
    assert loads[0] == 1
    # settled: later runs (and a restart: the function keeps no state) do not replay again
    assert (await apply_due_corporate_actions(repo, at(TUE, 8, 40))).applied == []
    assert (await apply_due_corporate_actions(repo, at(TUE, 16, 5))).applied == []
    assert loads[0] == 1
    # the marker is not a corporate action: nothing for the REST list / UI
    assert await repo.list_paper_corporate_actions(account.id, epoch=None) == []
    [check] = await repo.list_paper_dividend_checks()
    assert (check.symbol, check.ex_date, check.source_revision) == (SYMBOL, TUE, 1)
    # a revision is evaluated again once, then settled at the new revision
    await repo.upsert_dividend_events([event(TUE, cash=1.2, revision=2, available=at(TUE, 9))])
    assert (await apply_due_corporate_actions(repo, at(TUE, 16, 10))).applied == []
    assert loads[0] == 2
    assert (await apply_due_corporate_actions(repo, at(TUE, 16, 40))).applied == []
    assert loads[0] == 2
    assert [c.source_revision for c in await repo.list_paper_dividend_checks()] == [2]


async def test_a_revised_event_that_now_pays_is_applied_after_a_no_payout_check() -> None:
    repo, _, _, account = await seeded(ex_cash=None, ex_stock=None)
    await repo.upsert_dividend_events([event(MON, cash=0.0)])  # announced without an amount
    assert (await apply_due_corporate_actions(repo, at(MON, 8, 5))).applied == []
    assert len(await repo.list_paper_dividend_checks()) == 1
    await repo.upsert_dividend_events([event(MON, cash=2.5, revision=2, available=at(MON, 14))])
    report = await apply_due_corporate_actions(repo, at(MON, 14, 5))
    assert report.applied == [(account.id, MON)]
    [stored] = await repo.list_paper_corporate_actions(account.id, epoch=0)
    assert (stored.entitled_shares, stored.cash_amount, stored.source_revision) == (2000, 5000, 2)


async def test_one_run_settles_several_events_in_one_walk_and_sees_its_own_bonus() -> None:
    repo, _, _, account = await seeded()  # Monday: 2.5 cash + 0.05 stock on 2,000 shares
    await repo.upsert_dividend_events([event(TUE, cash=1.0)])
    loads = _count_fill_loads(repo)
    report = await apply_due_corporate_actions(repo, at(TUE, 8, 5))
    assert sorted(d for _, d in report.applied) == [MON, TUE] and loads[0] == 1
    tue = [a for a in await repo.list_paper_corporate_actions(account.id, epoch=0)]
    assert [(a.ex_date, a.entitled_shares) for a in tue] == [(MON, 2000), (TUE, 2100)]


def test_book_cursor_matches_replay_at_every_bound() -> None:
    from quant_signal.quant.paper_book import BookCursor
    from quant_signal.quant.paper_dividends import entitled_at

    fills = [
        make_fill("buy", 2000, "100", at(THU, 10)),
        make_fill("sell", 1000, "100", at(MON, 10)),
        make_fill("buy", 3000, "100", at(TUE, 9)),
    ]
    actions = [make_action(MON, added=100), make_action(WED, added=50)]
    cursor = BookCursor(fills, actions)
    for day in (FRI, MON, TUE, WED, date(2026, 12, 3)):
        assert entitled_at(cursor, SYMBOL, day) == entitled_shares(fills, actions, SYMBOL, day)
    late = make_action(TUE, added=7)  # behind the cursor: it rewinds and stays exact
    cursor.add_action(late)
    assert entitled_at(cursor, SYMBOL, date(2026, 12, 3)) == entitled_shares(
        fills, [*actions, late], SYMBOL, date(2026, 12, 3)
    )


async def test_a_failed_snapshot_rewrite_is_retried_after_a_restart() -> None:
    repo, _, _, account = await seeded(ex_cash=None, ex_stock=None)
    await repo.upsert_market_stock_days(
        [stock_row(SYMBOL, d, close=100.0, name="台泥") for d in (FRI, MON, TUE)]
    )
    jobs = PaperJobs()
    for day in (FRI, MON, TUE):
        snap, _ = await jobs.snapshot_account(repo, account, day, at(day, 15))
        await repo.upsert_paper_snapshot(snap)
    await repo.upsert_dividend_events([event(MON, cash=2.5, available=at(WED, 7))])

    async def broken(*args, **kwargs):
        raise RuntimeError("database went away")

    jobs._rewrite_snapshots = broken  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await jobs.apply_corporate_actions(repo, at(WED, 16, 5))
    assert len(await repo.list_paper_corporate_actions(account.id, epoch=0)) == 1  # booked
    assert jobs._actions_done_day is None  # a failed pass is not "done for the day"
    stale = {s.trading_date: s for s in await repo.list_paper_snapshots(account.id, epoch=0)}
    assert stale[MON].cash == 799_715

    restarted = PaperJobs()  # nothing in memory: the days come from the database
    stats = await restarted.apply_corporate_actions(repo, at(WED, 16, 35))
    assert stats.dividends == 0
    after = {s.trading_date: s for s in await repo.list_paper_snapshots(account.id, epoch=0)}
    assert after[MON].cash == after[TUE].cash == 799_715 + 5000
    assert after[FRI].cash == 799_715
    assert restarted._actions_done_day == WED
    assert await repo.list_paper_actions_to_revalue() == []  # settled: not rewritten again
