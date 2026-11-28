"""ORM rows for paper trading (migration 0018). Registered on the shared ``Base``."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from quant_signal.infrastructure.db.models import Base


class PaperAccountRow(Base):
    __tablename__ = "paper_accounts"
    __table_args__ = (
        # Names are unique among a user's live accounts; an archived name can be reused.
        Index(
            "uq_paper_accounts_user_name",
            "user_id",
            "name",
            unique=True,
            postgresql_where=text("NOT archived"),
        ),
        CheckConstraint(
            "commission_discount >= 0.1 AND commission_discount <= 1", name="ck_paper_discount"
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    initial_cash: Mapped[int] = mapped_column(BigInteger, nullable=False)
    commission_discount: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, server_default=text("1.0")
    )
    allow_day_trade: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Bumped by every reset; fills, orders and the ledger of older epochs stay readable
    # but no longer count toward cash or positions.
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class PaperOrderRow(Base):
    __tablename__ = "paper_orders"
    __table_args__ = (
        Index("ix_paper_orders_account_created", "account_id", "created_at"),
        Index("ix_paper_orders_working", "status", postgresql_where=text("status = 'working'")),
        CheckConstraint("lots > 0", name="ck_paper_orders_lots"),
        CheckConstraint(
            "status IN ('working','filled','cancelled','expired','rejected')",
            name="ck_paper_orders_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False
    )
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    order_type: Mapped[str] = mapped_column(String(8), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    lots: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'manual'"))
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    reserved_cash: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    reserve_amount: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    note: Mapped[str | None] = mapped_column(Text)


class PaperFillRow(Base):
    __tablename__ = "paper_fills"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_paper_fills_order"),
        Index("ix_paper_fills_account_filled", "account_id", "filled_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    order_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("paper_orders.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False
    )
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    commission: Mapped[int] = mapped_column(Integer, nullable=False)
    tax: Mapped[int] = mapped_column(Integer, nullable=False)
    day_trade: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price_source: Mapped[str] = mapped_column(String(12), nullable=False)
    price_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperLedgerRow(Base):
    __tablename__ = "paper_cash_ledger"
    __table_args__ = (
        # A fill posts each kind at most once, so replaying a booking cannot double-post.
        UniqueConstraint("fill_id", "kind", name="uq_paper_ledger_fill_kind"),
        # fill_id is NULL on dividend postings (NULLs never collide), so they are unique per
        # corporate action instead; and a dividend row must point at its action.
        UniqueConstraint("action_id", "kind", name="uq_paper_ledger_action_kind"),
        CheckConstraint(
            "(kind = 'dividend') = (action_id IS NOT NULL)", name="ck_paper_ledger_action"
        ),
        # A user's deposit / withdrawal (migration 0027) carries the client's request id:
        # unique per account, so a double click can never post twice.
        UniqueConstraint("account_id", "request_id", name="uq_paper_ledger_request"),
        CheckConstraint(
            "(kind IN ('transfer_in','transfer_out')) = (request_id IS NOT NULL)",
            name="ck_paper_ledger_request",
        ),
        CheckConstraint(
            "(kind <> 'transfer_in' OR amount > 0) AND (kind <> 'transfer_out' OR amount < 0)",
            name="ck_paper_ledger_transfer_sign",
        ),
        Index("ix_paper_ledger_account_epoch", "account_id", "epoch"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False
    )
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fill_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("paper_fills.id", ondelete="CASCADE")
    )
    action_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("paper_corporate_actions.id", ondelete="CASCADE")
    )
    request_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    note: Mapped[str | None] = mapped_column(Text)


class PaperSnapshotRow(Base):
    __tablename__ = "paper_daily_snapshots"

    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("paper_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # A reset starts a new epoch whose rows must not collide with (or overwrite) the old one.
    epoch: Mapped[int] = mapped_column(
        Integer, primary_key=True, nullable=False, server_default=text("0")
    )
    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    cash: Mapped[int] = mapped_column(BigInteger, nullable=False)
    market_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    equity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    realized_pnl_cum: Mapped[int] = mapped_column(BigInteger, nullable=False)
    benchmark_close: Mapped[float | None] = mapped_column(Float)
    # Net transfers dated in (previous trading day, trading_date] (migration 0027).
    net_transfer: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))


class PaperCorporateActionRow(Base):
    """One dividend event applied to one account epoch (migration 0024)."""

    __tablename__ = "paper_corporate_actions"
    __table_args__ = (
        # The idempotency key: an event can never be applied twice to the same epoch.
        UniqueConstraint("account_id", "epoch", "symbol", "ex_date", name="uq_paper_actions_event"),
        Index("ix_paper_actions_account_epoch", "account_id", "epoch", "ex_at"),
        CheckConstraint("entitled_shares > 0", name="ck_paper_actions_entitled"),
        CheckConstraint(
            "cash_amount >= 0 AND shares_added >= 0 AND (cash_amount > 0 OR shares_added > 0)",
            name="ck_paper_actions_payout",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False
    )
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    entitled_shares: Mapped[int] = mapped_column(Integer, nullable=False)
    cash_dividend_per_share: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    stock_dividend_per_share: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    cash_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    shares_added: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    ex_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When the equity snapshots from the ex-date on were re-valued with this action; NULL
    # until then, so a failed or interrupted re-valuation is retried from the database.
    revalued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaperDividendCheckRow(Base):
    """A dividend event evaluated for an account epoch with nothing due (migration 0024).

    Keyed like ``uq_paper_actions_event``; keeps the dividend job from re-evaluating the
    event every run. ``source_revision`` is the event revision it was evaluated against: a
    different revision is evaluated again.
    """

    __tablename__ = "paper_dividend_checks"

    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("paper_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    epoch: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    ex_date: Mapped[date] = mapped_column(Date, primary_key=True)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaperProposalRow(Base):
    """An AI trade proposal (migration 0026); ``payload`` is the rest of the served object."""

    __tablename__ = "paper_proposals"
    __table_args__ = (
        Index("ix_paper_proposals_account_created", "account_id", "created_at"),
        # One pending proposal per account and symbol: the safety net under "supersede".
        Index(
            "uq_paper_proposals_pending",
            "account_id",
            "symbol",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        # An order belongs to at most one proposal.
        Index(
            "uq_paper_proposals_order",
            "order_id",
            unique=True,
            postgresql_where=text("order_id IS NOT NULL"),
        ),
        CheckConstraint(
            "status IN ('pending','accepted','rejected','expired','superseded')",
            name="ck_paper_proposals_status",
        ),
        CheckConstraint("action IN ('buy','sell','hold')", name="ck_paper_proposals_action"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False
    )
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    action: Mapped[str] = mapped_column(String(4), nullable=False)
    side: Mapped[str | None] = mapped_column(String(4))
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    lots: Mapped[int | None] = mapped_column(Integer)
    order_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("paper_orders.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
