"""Deposits and withdrawals on real Postgres: races, idempotency, constraints, round trips.

Skipped unless ``QUANT_TEST_DATABASE_URL`` points at a database migrated to head.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from quant_signal.application.paper_transfers import (
    chain_flows,
    taipei_day_bounds,
    twr_return_pct,
)
from quant_signal.domain.paper import (
    LedgerEntry,
    PaperAccountRecord,
    PaperOrderRecord,
    SnapshotRecord,
)
from quant_signal.infrastructure.db.models import UserRow
from quant_signal.infrastructure.db.repository import PostgresQuantRepository

DATABASE_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="QUANT_TEST_DATABASE_URL not set")

NOW = datetime.now(UTC)
START, END = taipei_day_bounds(NOW)


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(DATABASE_URL)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    async with factory() as session:
        await session.execute(text("DELETE FROM users WHERE google_sub LIKE 'zz-xfer-%'"))
        await session.commit()
    await engine.dispose()


async def make_account(sessions, cash=1_000_000) -> PaperAccountRecord:
    user_id = uuid4()
    async with sessions() as session:
        session.add(
            UserRow(id=user_id, google_sub=f"zz-xfer-{uuid4().hex}", email="zz@example.com")
        )
        await session.commit()
        account = PaperAccountRecord(user_id=user_id, name="t", initial_cash=cash, created_at=NOW)
        assert (
            await PostgresQuantRepository(session).create_paper_account(account, max_accounts=3)
            == "created"
        )
    return account


def entry(account, amount, *, request_id=None, at=None, epoch=None) -> LedgerEntry:
    return LedgerEntry(
        account_id=account.id,
        epoch=account.epoch if epoch is None else epoch,
        at=at or NOW,
        kind="transfer_in" if amount > 0 else "transfer_out",
        amount=amount,
        request_id=request_id or uuid4(),
    )


async def post(sessions, e, *, max_per_day=20, max_cash=1_000_000_000):
    async with sessions() as session:
        return await PostgresQuantRepository(session).apply_paper_transfer(
            e, day_start=START, day_end=END, max_per_day=max_per_day, max_cash=max_cash
        )


async def cash_of(sessions, account, epoch=0) -> int:
    async with sessions() as session:
        return await PostgresQuantRepository(session).paper_cash(account.id, epoch)


async def test_deposit_withdrawal_and_ledger_round_trip(sessions) -> None:
    account = await make_account(sessions)
    assert (
        await post(sessions, entry(account, 250_000, at=NOW + timedelta(seconds=1)))
    ).status == "ok"
    out = await post(sessions, entry(account, -100_000, at=NOW + timedelta(seconds=2)))
    assert out.status == "ok" and out.cash == 1_150_000
    assert await cash_of(sessions, account) == 1_150_000
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        listed = await repo.list_paper_transfer_entries(account.id, epoch=0)
        assert [(e.kind, e.amount) for e in listed] == [
            ("transfer_in", 250_000),
            ("transfer_out", -100_000),
        ]
        assert all(e.request_id is not None for e in listed)
        ledger = await repo.list_paper_ledger(account.id, 0)
        assert [e.kind for e in ledger] == ["deposit", "transfer_in", "transfer_out"]


async def test_concurrent_duplicate_request_ids_book_one_row(sessions) -> None:
    account = await make_account(sessions)
    rid = uuid4()
    results = await asyncio.gather(
        *(post(sessions, entry(account, 100_000, request_id=rid)) for _ in range(6))
    )
    assert sorted(r.status for r in results) == ["ok"] + ["replayed"] * 5
    assert {r.entry.id for r in results if r.entry} == {
        next(r.entry.id for r in results if r.status == "ok")
    }
    assert await cash_of(sessions, account) == 1_100_000
    async with sessions() as session:
        assert (
            len(
                await PostgresQuantRepository(session).list_paper_transfer_entries(
                    account.id, epoch=0
                )
            )
            == 1
        )


async def test_concurrent_withdrawals_cannot_overdraw(sessions) -> None:
    account = await make_account(sessions)
    results = await asyncio.gather(*(post(sessions, entry(account, -400_000)) for _ in range(6)))
    statuses = sorted(r.status for r in results)
    assert statuses == [
        "ok",
        "ok",
        "over_available",
        "over_available",
        "over_available",
        "over_available",
    ]
    assert await cash_of(sessions, account) == 200_000


async def test_withdrawal_respects_working_buy_reserves_and_races_with_placement(sessions) -> None:
    account = await make_account(sessions)
    async with sessions() as session:
        await PostgresQuantRepository(session).add_paper_order(
            PaperOrderRecord(
                account_id=account.id,
                symbol="ZZ1.TW",
                name="x",
                side="buy",
                order_type="limit",
                limit_price=100,
                lots=1,
                session_date=date.today(),
                created_at=NOW,
                updated_at=NOW,
                reserved_cash=300_000,
                reserve_amount=299_500,
            )
        )
    refused = await post(sessions, entry(account, -700_001))
    assert refused.status == "over_available" and refused.available == 700_000
    assert (await post(sessions, entry(account, -700_000))).status == "ok"


async def test_daily_cap_ceiling_stale_epoch_and_missing(sessions) -> None:
    account = await make_account(sessions)
    for _ in range(3):
        assert (await post(sessions, entry(account, 1_000), max_per_day=3)).status == "ok"
    assert (await post(sessions, entry(account, 1_000), max_per_day=3)).status == "daily_cap"
    ceiling = await post(sessions, entry(account, 5_000), max_cash=1_002_000)
    assert ceiling.status == "ceiling"
    assert (await post(sessions, entry(account, 1_000, epoch=7))).status == "stale"
    ghost = PaperAccountRecord(user_id=uuid4(), name="g", initial_cash=1, created_at=NOW)
    assert (await post(sessions, entry(ghost, 1_000))).status == "missing"


async def test_reset_with_a_new_amount_and_a_replay_across_epochs(sessions) -> None:
    account = await make_account(sessions)
    rid = uuid4()
    assert (await post(sessions, entry(account, 200_000, request_id=rid))).status == "ok"
    async with sessions() as session:
        fresh = await PostgresQuantRepository(session).reset_paper_account(
            account.id, now=NOW + timedelta(seconds=1), initial_cash=4_000_000
        )
    assert fresh is not None and fresh.epoch == 1 and fresh.initial_cash == 4_000_000
    assert await cash_of(sessions, account, 1) == 4_000_000
    again = await post(sessions, entry(account, 200_000, request_id=rid, epoch=1))
    assert again.status == "replayed" and again.entry.epoch == 0
    assert await cash_of(sessions, account, 1) == 4_000_000
    async with sessions() as session:
        assert (
            await PostgresQuantRepository(session).list_paper_transfer_entries(account.id, epoch=1)
            == []
        )


async def test_the_database_itself_refuses_bad_transfer_rows(sessions) -> None:
    account = await make_account(sessions)

    async def raw(statement: str, **params) -> None:
        async with sessions() as session:
            await session.execute(text(statement), {"a": account.id, "now": NOW, **params})
            await session.commit()

    insert = (
        "INSERT INTO paper_cash_ledger (id, account_id, epoch, at, kind, amount, request_id) "
        "VALUES (gen_random_uuid(), :a, 0, :now, :kind, :amount, :rid)"
    )
    rid = uuid4()
    await raw(insert, kind="transfer_in", amount=5_000, rid=rid)
    with pytest.raises(IntegrityError):  # same request id again
        await raw(insert, kind="transfer_in", amount=5_000, rid=rid)
    with pytest.raises(IntegrityError):  # a transfer must carry a request id
        await raw(insert, kind="transfer_in", amount=5_000, rid=None)
    with pytest.raises(IntegrityError):  # and nothing else may
        await raw(insert, kind="tax", amount=-5, rid=uuid4())
    with pytest.raises(IntegrityError):  # a deposit is positive
        await raw(insert, kind="transfer_in", amount=-5_000, rid=uuid4())
    with pytest.raises(IntegrityError):  # a withdrawal is negative
        await raw(insert, kind="transfer_out", amount=5_000, rid=uuid4())


async def test_a_reused_request_id_with_another_body_is_a_mismatch(sessions) -> None:
    account = await make_account(sessions)
    rid = uuid4()
    assert (await post(sessions, entry(account, 100_000, request_id=rid))).status == "ok"
    # the response was lost and the user edited the amount: refused, nothing booked
    edited = await post(sessions, entry(account, 500_000, request_id=rid))
    assert edited.status == "mismatch" and edited.entry.amount == 100_000
    flipped = await post(sessions, entry(account, -100_000, request_id=rid))
    assert flipped.status == "mismatch"
    # the unchanged retry replays
    assert (await post(sessions, entry(account, 100_000, request_id=rid))).status == "replayed"
    assert await cash_of(sessions, account) == 1_100_000


async def test_racing_different_bodies_under_one_request_id_book_one_row(sessions) -> None:
    account = await make_account(sessions)
    rid = uuid4()
    amounts = [100_000, 500_000, 100_000, 500_000]
    results = await asyncio.gather(
        *(post(sessions, entry(account, a, request_id=rid)) for a in amounts)
    )
    winner = next(r for r in results if r.status == "ok")
    for result, amount in zip(results, amounts, strict=True):
        if result is winner:
            continue
        expected = "replayed" if amount == winner.entry.amount else "mismatch"
        assert result.status == expected and result.entry.id == winner.entry.id
    assert await cash_of(sessions, account) == 1_000_000 + winner.entry.amount


async def test_snapshots_store_no_flow_and_the_chain_reads_it_from_the_ledger(sessions) -> None:
    account = await make_account(sessions)
    async with sessions() as session:
        columns = await session.scalars(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'paper_daily_snapshots'"
            )
        )
        assert "net_transfer" not in set(columns)
    fri, mon, tue = date(2026, 11, 27), date(2026, 11, 30), date(2026, 12, 1)
    taipei = ZoneInfo("Asia/Taipei")
    # Monday's row is missing (the job was down); Monday's deposit belongs to Tuesday's step
    deposit = entry(account, 1_000_000, at=datetime(2026, 11, 30, 10, tzinfo=taipei))
    evening = entry(account, 5_000, at=datetime(2026, 12, 1, 14, tzinfo=taipei))
    for e in (deposit, evening):
        async with sessions() as session:
            outcome = await PostgresQuantRepository(session).apply_paper_transfer(
                e,
                day_start=e.at - timedelta(hours=1),
                day_end=e.at + timedelta(hours=1),
                max_per_day=20,
                max_cash=1_000_000_000,
            )
            assert outcome.status == "ok"
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        for day, equity in ((fri, 1_000_000), (tue, 2_000_000)):
            snap = SnapshotRecord(
                account_id=account.id,
                trading_date=day,
                epoch=0,
                cash=equity,
                market_value=0,
                equity=equity,
                realized_pnl_cum=0,
            )
            await repo.upsert_paper_snapshot(snap)
        await repo.upsert_paper_snapshot(snap.model_copy(update={"equity": 2_000_000}))
        snaps = await repo.list_paper_snapshots(account.id, epoch=0)
        entries = await repo.list_paper_transfer_entries(account.id, epoch=0)
    assert [s.trading_date for s in snaps] == [fri, tue] and mon not in {
        s.trading_date for s in snaps
    }
    flows = chain_flows([s.trading_date for s in snaps], entries)
    assert flows == [0, 1_000_000]  # Tuesday's 14:00 deposit is after its close
    steps = [(s.equity, f) for s, f in zip(snaps, flows, strict=True)]
    assert twr_return_pct(1_000_000, steps) == 0.0
