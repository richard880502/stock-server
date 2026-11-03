"""Add whole-market daily stock quotes, institutional flows and credit balances."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_market_stock_daily"
down_revision: str | None = "0007_fetch_coverage_sync_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_stock_daily",
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("turnover_value", sa.Float(), nullable=False),
        sa.Column("change", sa.Float(), nullable=False, server_default="0"),
        sa.Column("foreign_net", sa.BigInteger(), nullable=True),
        sa.Column("trust_net", sa.BigInteger(), nullable=True),
        sa.Column("dealer_net", sa.BigInteger(), nullable=True),
        sa.Column("total_net", sa.BigInteger(), nullable=True),
        sa.Column("margin_balance", sa.BigInteger(), nullable=True),
        sa.Column("margin_change", sa.BigInteger(), nullable=True),
        sa.Column("short_balance", sa.BigInteger(), nullable=True),
        sa.Column("short_change", sa.BigInteger(), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "trading_date", "source"),
    )
    op.create_index(
        "ix_market_stock_daily_date_market",
        "market_stock_daily",
        ["trading_date", "market"],
    )


def downgrade() -> None:
    op.drop_index("ix_market_stock_daily_date_market", table_name="market_stock_daily")
    op.drop_table("market_stock_daily")
