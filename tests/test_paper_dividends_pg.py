"""Paper dividends against real PostgreSQL: the constraints, not just the application code.

Skipped unless ``QUANT_TEST_DATABASE_URL`` points at a migrated, dedicated database.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from paper_support import FRI, MON, at
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from quant_signal.application.paper_actions import apply_due_corporate_actions, load_book
from quant_signal.application.paper_jobs import PaperJobs, ledger_entries
from quant_signal.domain.paper import PaperAccountRecord, PaperFillRecord, PaperOrderRecord
from quant_signal.domain.paper_actions import CorporateActionRecord
from quant_signal.domain.short_data import DividendEvent
from quant_signal.infrastructure.db import PostgresQuantRepository
from quant_signal.infrastructure.db.models import UserRow
from quant_signal.quant.paper_dividends import ex_at

DATABASE_URL = os.environ.get("QUANT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="QUANT_TEST_DATABASE_URL not set")


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(DATABASE_URL)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    async with factory() as session:
        await session.execute(text("DELETE FROM users WHERE google_sub LIKE 'zz-div-%'"))
        await session.execute(text("DELETE FROM dividend_events WHERE symbol LIKE 'ZZ%'"))
        await session.commit()
    await engine.dispose()


async def account_with_shares(sessions, symbol: str, *, shares: int = 2000):
    """An account created on Friday morning that bought ``shares`` on Friday 10:00."""
    user_id = uuid4()
    async with sessions() as session:
        session.add(UserRow(id=user_id, google_sub=f"zz-div-{uuid4().hex}", email="d@example.com"))
        await session.commit()
        repo = PostgresQuantRepository(session)
        account = PaperAccountRecord(
            user_id=user_id, name="div", initial_cash=1_000_000, created_at=at(FRI, 9, 30)
        )
        assert await repo.create_paper_account(account, max_accounts=3) == "created"
        order = PaperOrderRecord(
            account_id=account.id,
            symbol=symbol,
            name="x",
            side="buy",
            order_type="market",
            lots=shares // 1000,
            session_date=FRI,
            created_at=at(FRI, 9, 31),
            updated_at=at(FRI, 9, 31),
            reserved_cash=shares * 101,
        )
        await repo.add_paper_order(order)
        fill = PaperFillRecord(
            order_id=order.id,
            account_id=account.id,
            symbol=symbol,
            side="buy",
            shares=shares,
            price=Decimal("100"),
            amount=shares * 100,
            commission=285,
            tax=0,
            filled_at=at(FRI, 10),
            price_source="tick",
            price_time=at(FRI, 10),
        )
        assert await repo.apply_paper_fill(fill, ledger_entries(fill, at(FRI, 10)), now=at(FRI, 10))
    return account


def action_for(account, symbol: str, **overrides) -> CorporateActionRecord:
    values = dict(
        account_id=account.id,
        epoch=account.epoch,
        symbol=symbol,
        ex_date=MON,
        event_kind="ex_dividend",
        entitled_shares=2000,
        cash_dividend_per_share=Decimal("2.5"),
        cash_amount=5000,
        shares_added=0,
        ex_at=ex_at(MON),
        applied_at=at(MON, 8, 5),
        source="twse_official",
        source_revision=1,
        source_available_at=at(FRI, 8),
    )
    values.update(overrides)
    return CorporateActionRecord(**values)


def symbol() -> str:
    return f"ZZ{uuid4().hex[:8].upper()}.TW"


async def test_two_concurrent_applications_of_one_event_book_exactly_once(sessions) -> None:
    sym = symbol()
    account = await account_with_shares(sessions, sym)

    async def attempt() -> bool:
        async with sessions() as session:
            return await PostgresQuantRepository(session).apply_paper_corporate_action(
                action_for(account, sym)  # a fresh id each time: only the event key can stop it
            )

    results = await asyncio.gather(attempt(), attempt(), attempt())
    assert sorted(results) == [False, False, True]
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        assert len(await repo.list_paper_corporate_actions(account.id, epoch=0)) == 1
        ledger = [e for e in await repo.list_paper_ledger(account.id, 0) if e.kind == "dividend"]
        assert [e.amount for e in ledger] == [5000]
        assert await repo.paper_cash(account.id, 0) == 1_000_000 - 200_285 + 5000


async def test_the_database_itself_refuses_double_booking(sessions) -> None:
    sym = symbol()
    account = await account_with_shares(sessions, sym)
    first = action_for(account, sym)
    async with sessions() as session:
        assert await PostgresQuantRepository(session).apply_paper_corporate_action(first)

    async def raw(statement: str, **params) -> None:
        async with sessions() as session:
            await session.execute(text(statement), params)
            await session.commit()

    insert_action = (
        "INSERT INTO paper_corporate_actions (id, account_id, epoch, symbol, ex_date, event_kind, "
        "entitled_shares, cash_amount, shares_added, ex_at, applied_at, source, source_revision, "
        "source_available_at) VALUES (:id, :a, 0, :s, :d, 'ex_dividend', 10, 1, 0, now(), now(), "
        "'t', 1, now())"
    )
    with pytest.raises(IntegrityError):  # same (account, epoch, symbol, ex_date)
        await raw(insert_action, id=uuid4(), a=account.id, s=sym, d=MON)
    await raw(insert_action, id=uuid4(), a=account.id, s=sym, d=date(2026, 12, 7))  # other day: ok
    insert_ledger = (
        "INSERT INTO paper_cash_ledger (id, account_id, epoch, at, kind, amount, action_id) "
        "VALUES (:id, :a, 0, now(), :k, 1, :action)"
    )
    with pytest.raises(IntegrityError):  # a second dividend posting of the SAME action
        await raw(insert_ledger, id=uuid4(), a=account.id, k="dividend", action=first.id)
    with pytest.raises(IntegrityError):  # a dividend posting must name its action
        await raw(insert_ledger, id=uuid4(), a=account.id, k="dividend", action=None)
    with pytest.raises(IntegrityError):  # and only dividends may
        await raw(insert_ledger, id=uuid4(), a=account.id, k="deposit", action=first.id)
    with pytest.raises(IntegrityError):  # an action pays something and to somebody
        await raw(
            insert_action.replace("10, 1, 0", "10, 0, 0"),
            id=uuid4(),
            a=account.id,
            s=sym,
            d=date(2026, 12, 8),
        )


async def test_a_stale_epoch_or_archived_account_cannot_be_credited(sessions) -> None:
    sym = symbol()
    account = await account_with_shares(sessions, sym)
    async with sessions() as session:
        await PostgresQuantRepository(session).reset_paper_account(account.id, now=at(MON, 7))
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        assert await repo.apply_paper_corporate_action(action_for(account, sym)) is False
        assert await repo.list_paper_corporate_actions(account.id, epoch=None) == []


async def test_job_end_to_end_idempotent_and_bonus_shares_stay_open(sessions) -> None:
    sym = symbol()
    account = await account_with_shares(sessions, sym)
    event = DividendEvent(
        symbol=sym,
        ex_date=MON,
        kind="ex_rights_dividend",
        source="twse_official",
        cash_dividend_per_share=2.5,
        stock_dividend_per_share=0.05,
        available_at=datetime(2026, 11, 26, tzinfo=UTC),
    )
    async with sessions() as session:
        await PostgresQuantRepository(session).upsert_dividend_events([event])
    now = at(MON, 8, 5)
    for _ in range(2):  # twice, each on a fresh session and a fresh PaperJobs
        async with sessions() as session:
            await PaperJobs().tick(PostgresQuantRepository(session), now)
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        (stored,) = await repo.list_paper_corporate_actions(account.id, epoch=0)
        assert (stored.entitled_shares, stored.cash_amount, stored.shares_added) == (
            2000,
            5000,
            100,
        )
        assert stored.cash_dividend_per_share == Decimal("2.5") and stored.source_revision == 1
        book = await load_book(repo, account.id, 0)
        assert book.held(sym) == 2100 and book.dividend_income == 5000
        assert sym in await repo.list_open_position_symbols()
        assert (sym_pairs := await repo.list_paper_traded_pairs()) and (
            account.id,
            sym,
        ) in sym_pairs
    async with sessions() as session:
        report = await apply_due_corporate_actions(PostgresQuantRepository(session), at(MON, 20))
        assert report.applied == []


async def test_no_payout_checks_upsert_and_revaluation_marks(sessions) -> None:
    from quant_signal.domain.paper_actions import DividendCheckRecord

    sym = symbol()
    account = await account_with_shares(sessions, sym)
    async with sessions() as session:
        repo = PostgresQuantRepository(session)
        for revision in (1, 2):  # the second write updates the same key, never a duplicate
            await repo.record_paper_dividend_check(
                DividendCheckRecord(
                    account_id=account.id,
                    symbol=sym,
                    ex_date=MON,
                    source_revision=revision,
                    evaluated_at=at(MON, 8, revision),
                )
            )
        mine = [c for c in await repo.list_paper_dividend_checks() if c.account_id == account.id]
        assert [(c.symbol, c.ex_date, c.source_revision) for c in mine] == [(sym, MON, 2)]
        assert await repo.list_paper_corporate_actions(account.id, epoch=None) == []

        action = action_for(account, sym)
        assert await repo.apply_paper_corporate_action(action)
        assert action.id in {a.id for a in await repo.list_paper_actions_to_revalue()}
        assert action.id in {a.id for a in await repo.list_active_paper_corporate_actions()}
        await repo.mark_paper_actions_revalued([action.id], now=at(MON, 8, 6))
        assert action.id not in {a.id for a in await repo.list_paper_actions_to_revalue()}
        (stored,) = await repo.list_paper_corporate_actions(account.id, epoch=0)
        assert stored.revalued_at == at(MON, 8, 6)
