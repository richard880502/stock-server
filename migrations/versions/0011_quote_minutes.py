"""Captured Shioaji minute bars, separate from official daily history."""

import sqlalchemy as sa
from alembic import op

revision = "0011_quote_minutes"
down_revision = "0010_watchlist_items"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "quote_minutes",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("minute", sa.DateTime(timezone=True), primary_key=True),
        *(sa.Column(name, sa.Float, nullable=False) for name in ("open", "high", "low", "close")),
        sa.Column("first_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_total_volume", sa.BigInteger, nullable=False),
        sa.Column("last_total_volume", sa.BigInteger, nullable=False),
        sa.Column("is_partial", sa.Boolean, nullable=False),
    )


def downgrade():
    op.drop_table("quote_minutes")
