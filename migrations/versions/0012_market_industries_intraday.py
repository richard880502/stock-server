"""Add stock industry classification and 10-minute intraday snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0012_market_industries_intraday"
down_revision = "0011_quote_minutes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "market_industries",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("industry_code", sa.String(8), nullable=False),
        sa.Column("industry_name", sa.String(64), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "market_intraday_snapshots",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("taken_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("price", sa.Float, nullable=False),
        sa.Column("change_pct", sa.Float, nullable=False),
        sa.Column("volume_lots", sa.BigInteger, nullable=False),
        sa.Column("amount_twd", sa.Float, nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_market_intraday_taken_at", "market_intraday_snapshots", ["taken_at"]
    )


def downgrade():
    op.drop_index("ix_market_intraday_taken_at", table_name="market_intraday_snapshots")
    op.drop_table("market_intraday_snapshots")
    op.drop_table("market_industries")
