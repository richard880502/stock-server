"""Paper trading (模擬交易): accounts, orders, fills, cash ledger, daily snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_paper_trading"
down_revision: str | None = "0015_market_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "paper_accounts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("initial_cash", sa.BigInteger, nullable=False),
        sa.Column(
            "commission_discount", sa.Numeric(5, 4), server_default=sa.text("1.0"), nullable=False
        ),
        sa.Column("allow_day_trade", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("epoch", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "commission_discount >= 0.1 AND commission_discount <= 1", name="ck_paper_discount"
        ),
    )
    op.create_index("ix_paper_accounts_user_id", "paper_accounts", ["user_id"])
    op.create_index(
        "uq_paper_accounts_user_name",
        "paper_accounts",
        ["user_id", "name"],
        unique=True,
        postgresql_where=sa.text("NOT archived"),
    )

    op.create_table(
        "paper_orders",
        sa.Column("id", UUID, nullable=False),
        sa.Column("account_id", UUID, nullable=False),
        sa.Column("epoch", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("order_type", sa.String(8), nullable=False),
        sa.Column("limit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("lots", sa.Integer, nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("source", sa.String(8), server_default=sa.text("'manual'"), nullable=False),
        sa.Column("session_date", sa.Date, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reject_reason", sa.Text, nullable=True),
        sa.Column("reserved_cash", sa.BigInteger, server_default=sa.text("0"), nullable=False),
        sa.Column("note", sa.Text, nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("lots > 0", name="ck_paper_orders_lots"),
        sa.CheckConstraint(
            "status IN ('working','filled','cancelled','expired','rejected')",
            name="ck_paper_orders_status",
        ),
    )
    op.create_index("ix_paper_orders_account_created", "paper_orders", ["account_id", "created_at"])
    op.create_index(
        "ix_paper_orders_working",
        "paper_orders",
        ["status"],
        postgresql_where=sa.text("status = 'working'"),
    )

    op.create_table(
        "paper_fills",
        sa.Column("id", UUID, nullable=False),
        sa.Column("order_id", UUID, nullable=False),
        sa.Column("account_id", UUID, nullable=False),
        sa.Column("epoch", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("shares", sa.Integer, nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("commission", sa.Integer, nullable=False),
        sa.Column("tax", sa.Integer, nullable=False),
        sa.Column("day_trade", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price_source", sa.String(12), nullable=False),
        sa.Column("price_time", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["paper_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", name="uq_paper_fills_order"),
    )
    op.create_index("ix_paper_fills_account_filled", "paper_fills", ["account_id", "filled_at"])

    op.create_table(
        "paper_cash_ledger",
        sa.Column("id", UUID, nullable=False),
        sa.Column("account_id", UUID, nullable=False),
        sa.Column("epoch", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("fill_id", UUID, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["fill_id"], ["paper_fills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fill_id", "kind", name="uq_paper_ledger_fill_kind"),
    )
    op.create_index("ix_paper_ledger_account_epoch", "paper_cash_ledger", ["account_id", "epoch"])

    op.create_table(
        "paper_daily_snapshots",
        sa.Column("account_id", UUID, nullable=False),
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("epoch", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("cash", sa.BigInteger, nullable=False),
        sa.Column("market_value", sa.BigInteger, nullable=False),
        sa.Column("equity", sa.BigInteger, nullable=False),
        sa.Column("realized_pnl_cum", sa.BigInteger, nullable=False),
        sa.Column("benchmark_close", sa.Float, nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "trading_date"),
    )


def downgrade() -> None:
    op.drop_table("paper_daily_snapshots")
    op.drop_index("ix_paper_ledger_account_epoch", table_name="paper_cash_ledger")
    op.drop_table("paper_cash_ledger")
    op.drop_index("ix_paper_fills_account_filled", table_name="paper_fills")
    op.drop_table("paper_fills")
    op.drop_index("ix_paper_orders_working", table_name="paper_orders")
    op.drop_index("ix_paper_orders_account_created", table_name="paper_orders")
    op.drop_table("paper_orders")
    op.drop_index("uq_paper_accounts_user_name", table_name="paper_accounts")
    op.drop_index("ix_paper_accounts_user_id", table_name="paper_accounts")
    op.drop_table("paper_accounts")
