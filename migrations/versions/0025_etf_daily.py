"""ETF end-of-day quotes, separate from the stock-only market_stock_daily."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_etf_daily"
down_revision: str | None = "0024_paper_corporate_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "etf_daily",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("open", sa.Float, nullable=False),
        sa.Column("high", sa.Float, nullable=False),
        sa.Column("low", sa.Float, nullable=False),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("change", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("volume", sa.BigInteger, nullable=False),
        sa.Column("turnover_value", sa.Float, nullable=False),
        sa.Column("trades", sa.BigInteger, nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "trading_date"),
    )
    op.create_index("ix_etf_daily_date", "etf_daily", ["trading_date"])


def downgrade() -> None:
    op.drop_index("ix_etf_daily_date", table_name="etf_daily")
    op.drop_table("etf_daily")
