"""Whole-market multi-factor screener snapshot (one row per stock, date and version)."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_market_factor_daily"
down_revision = "0015_market_events"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "market_factor_daily",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("industry_code", sa.String(8), nullable=True),
        sa.Column("industry_name", sa.String(64), nullable=True),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("change_pct", sa.Float, nullable=True),
        sa.Column("avg_turnover_20d", sa.Float, nullable=True),
        sa.Column("composite", sa.Float, nullable=True),
        sa.Column("scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("highlights", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("flow_through", sa.Date, nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "trading_date", "version"),
    )
    op.create_index(
        "ix_market_factor_daily_date_version",
        "market_factor_daily",
        ["trading_date", "version"],
    )


def downgrade():
    op.drop_index("ix_market_factor_daily_date_version", table_name="market_factor_daily")
    op.drop_table("market_factor_daily")
