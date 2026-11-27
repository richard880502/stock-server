"""TDCC weekly shareholding distribution and daily foreign holding / shares outstanding.

Chained directly after 0015 in this branch; 0016 (market_factor_daily) is added by a
parallel branch and the integrator re-chains this revision after it.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_holders_data"
down_revision = "0015_market_events"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shareholding_distribution_weekly",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("data_date", sa.Date, nullable=False),
        sa.Column("total_holders", sa.BigInteger, nullable=False),
        sa.Column("total_shares", sa.BigInteger, nullable=False),
        sa.Column("holders_ge400", sa.BigInteger, nullable=False),
        sa.Column("pct_ge400", sa.Float, nullable=False),
        sa.Column("holders_ge1000", sa.BigInteger, nullable=False),
        sa.Column("pct_ge1000", sa.Float, nullable=False),
        sa.Column("retail_holders", sa.BigInteger, nullable=False),
        sa.Column("retail_pct", sa.Float, nullable=False),
        sa.Column("tiers", postgresql.JSONB, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "data_date"),
    )
    op.create_index(
        "ix_shareholding_weekly_date", "shareholding_distribution_weekly", ["data_date"]
    )

    op.create_table(
        "foreign_holding_daily",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("shares_outstanding", sa.BigInteger, nullable=False),
        sa.Column("foreign_shares", sa.BigInteger, nullable=False),
        sa.Column("foreign_holding_pct", sa.Float, nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "trading_date", "source"),
    )
    op.create_index("ix_foreign_holding_date", "foreign_holding_daily", ["trading_date"])


def downgrade():
    op.drop_index("ix_foreign_holding_date", table_name="foreign_holding_daily")
    op.drop_table("foreign_holding_daily")
    op.drop_index("ix_shareholding_weekly_date", table_name="shareholding_distribution_weekly")
    op.drop_table("shareholding_distribution_weekly")
