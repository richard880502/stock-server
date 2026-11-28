"""AI proposals on real Postgres: the accept race, supersede, epoch reset, payload round trip.

Skipped unless ``QUANT_TEST_DATABASE_URL`` points at a database migrated to head.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from quant_signal.domain.paper import PaperAccountRecord, PaperOrderRecord
from quant_signal.domain.paper_proposals import ProposalRecord
from quant_signal.infrastructure.db.models import UserRow
from quant_signal.infrastructure.db.repository import PostgresQuantRepository

DATABASE_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="QUANT_TEST_DATABASE_URL not set")

NOW = datetime.now(UTC)
SYMBOL = "ZZ9.TW"


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(DATABASE_URL)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    async with factory() as session:
        await session.execute(text("DELETE FROM users WHERE google_sub LIKE 'zz-prop-%'"))
        await session.commit()
    await engine.dispose()


async def make_account(sessions) -> PaperAccountRecord:
    user_id = uuid4()
    async with sessions() as session:
        session.add(
            UserRow(id=user_id, google_sub=f"zz-prop-{uuid4().hex}", email="zz@example.com")
        )
        await session.commit()
        account = PaperAccountRecord(
            user_id=user_id, name="t", initial_cash=1_000_000, created_at=NOW
        )
        assert (
            await PostgresQuantRepository(session).create_paper_account(account, max_accounts=3)
            == "created"
        )
    return account


def proposal(account: PaperAccountRecord, **overrides) -> ProposalRecord:
    base = {
        "user_id": account.user_id,
        "account_id": account.id,
        "epoch": account.epoch,
        "symbol": SYMBOL,
        "name": "測試股",
        "as_of": date(2026, 11, 26),
        "created_at": NOW,
        "expires_at": NOW + timedelta(hours=4),
        "action": "buy",
        "side": "buy",
        "limit_price": Decimal("100.5"),
        "lots": 1,
        "payload": {"headline": "標題 ✓", "checks": [{"code": "x", "level": "info"}], "n": 1.5},
    }
    base.update(overrides)
    return ProposalRecord(**base)


def order_for(account: PaperAccountRecord) -> PaperOrderRecord:
    return PaperOrderRecord(
        account_id=account.id,
        epoch=account.epoch,
        symbol=SYMBOL,
        name="測試股",
        side="buy",
        order_type="limit",
        limit_price=Decimal("100.5"),
        lots=1,
        source="ai",
        session_date=NOW.date(),
        created_at=NOW,
        updated_at=NOW,
    )


async def add(sessions, record: ProposalRecord) -> int:
    async with sessions() as session:
        return await PostgresQuantRepository(session).add_paper_proposal(record)


async def fetch(sessions, proposal_id: UUID) -> ProposalRecord:
    async with sessions() as session:
        found = await PostgresQuantRepository(session).get_paper_proposal(proposal_id)
    assert found is not None
    return found


async def order_count(sessions, account_id: UUID) -> int:
    async with sessions() as session:
        return int(
            await session.scalar(
                text("SELECT count(*) FROM paper_orders WHERE account_id = :a"), {"a": account_id}
            )
        )


async def test_record_round_trips_with_its_payload(sessions) -> None:
    account = await make_account(sessions)
    record = proposal(account)
    await add(sessions, record)
    stored = await fetch(sessions, record.id)
    assert stored.payload == record.payload and stored.limit_price == Decimal("100.50")
    assert stored.status == "pending" and stored.lots == 1 and stored.side == "buy"
    assert stored.expires_at == record.expires_at
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        listed, total = await repo.list_paper_proposals(
            account.id, epoch=0, statuses=["pending"], limit=10, offset=0
        )
        assert [p.id for p in listed] == [record.id] and total == 1
        assert await repo.list_paper_proposals(
            account.id, epoch=1, statuses=None, limit=10, offset=0
        ) == ([], 0)


async def test_two_accepts_book_exactly_one_order(sessions) -> None:
    account = await make_account(sessions)
    record = proposal(account)
    await add(sessions, record)

    async def accept(order: PaperOrderRecord) -> bool:
        async with sessions() as session:
            return await PostgresQuantRepository(session).accept_paper_proposal(
                record.id, order, now=NOW
            )

    orders = [order_for(account) for _ in range(6)]
    results = await asyncio.gather(*(accept(order) for order in orders))
    assert sorted(results) == [False] * 5 + [True]
    winner = orders[results.index(True)]
    stored = await fetch(sessions, record.id)
    assert stored.status == "accepted" and stored.order_id == winner.id
    assert await order_count(sessions, account.id) == 1  # the losers rolled their order back


async def test_accept_refuses_overdue_rejected_and_reset_proposals(sessions) -> None:
    account = await make_account(sessions)
    overdue = proposal(account, expires_at=NOW - timedelta(minutes=1))
    await add(sessions, overdue)
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        assert await repo.accept_paper_proposal(overdue.id, order_for(account), now=NOW) is False
        assert await repo.expire_paper_proposals(account.id, now=NOW) == 1
    assert (await fetch(sessions, overdue.id)).status == "expired"

    rejected = proposal(account, symbol="ZZ8.TW")
    await add(sessions, rejected)
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        assert await repo.reject_paper_proposal(rejected.id, now=NOW, reason="no") is True
        assert await repo.reject_paper_proposal(rejected.id, now=NOW, reason="again") is False
        assert await repo.accept_paper_proposal(rejected.id, order_for(account), now=NOW) is False
    stored = await fetch(sessions, rejected.id)
    assert stored.status == "rejected" and stored.reject_reason == "no"
    assert await order_count(sessions, account.id) == 0


async def test_reset_expires_pending_proposals_and_blocks_acceptance(sessions) -> None:
    account = await make_account(sessions)
    pending = proposal(account)
    await add(sessions, pending)
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        reset = await repo.reset_paper_account(account.id, now=NOW)
        assert reset is not None and reset.epoch == 1
    stored = await fetch(sessions, pending.id)
    assert stored.status == "expired" and stored.decided_at is not None
    async with sessions() as session:
        assert (
            await PostgresQuantRepository(session).accept_paper_proposal(
                pending.id, order_for(account), now=NOW
            )
            is False
        )
    # a proposal stamped with the old epoch cannot be accepted even if it were still pending
    async with sessions() as session:
        await session.execute(
            text("UPDATE paper_proposals SET status = 'pending' WHERE id = :i"), {"i": pending.id}
        )
        await session.commit()
    async with sessions() as session:
        assert (
            await PostgresQuantRepository(session).accept_paper_proposal(
                pending.id, order_for(account).model_copy(update={"epoch": 1}), now=NOW
            )
            is False
        )
    assert await order_count(sessions, account.id) == 0


async def test_supersede_is_atomic_and_one_pending_per_symbol(sessions) -> None:
    account = await make_account(sessions)
    first = proposal(account)
    assert await add(sessions, first) == 0
    second = proposal(account, created_at=NOW + timedelta(seconds=1))
    assert await add(sessions, second) == 1
    assert (await fetch(sessions, first.id)).status == "superseded"

    other_symbol = proposal(account, symbol="ZZ7.TW")
    assert await add(sessions, other_symbol) == 0

    racing = [proposal(account, created_at=NOW + timedelta(seconds=2 + i)) for i in range(4)]
    await asyncio.gather(*(add(sessions, record) for record in racing))
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        pending, _ = await repo.list_paper_proposals(
            account.id, epoch=0, statuses=["pending"], limit=50, offset=0
        )
        assert sorted(p.symbol for p in pending) == sorted([SYMBOL, "ZZ7.TW"])
        _, total = await repo.list_paper_proposals(
            account.id, epoch=0, statuses=None, limit=50, offset=0
        )
        assert total == 7


async def test_database_enforces_the_status_set_and_single_order_link(sessions) -> None:
    from sqlalchemy.exc import IntegrityError

    account = await make_account(sessions)
    one, two = proposal(account), proposal(account, symbol="ZZ6.TW")
    await add(sessions, one)
    await add(sessions, two)
    async with sessions() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text("UPDATE paper_proposals SET status = 'bogus' WHERE id = :i"), {"i": one.id}
            )
    order = order_for(account)
    async with sessions() as session:
        assert await PostgresQuantRepository(session).accept_paper_proposal(one.id, order, now=NOW)
    async with sessions() as session:
        with pytest.raises(IntegrityError):  # an order links to at most one proposal
            await session.execute(
                text("UPDATE paper_proposals SET order_id = :o WHERE id = :i"),
                {"o": order.id, "i": two.id},
            )
