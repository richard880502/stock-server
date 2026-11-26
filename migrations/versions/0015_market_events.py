"""Official structured market data: events, risk flags and monthly revenue.

Rows are point-in-time (``available_at`` = when we fetched them) and keep revisions.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_market_events"
down_revision = "0014_news_available_at"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "market_events",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("external_key", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("announced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("clause", sa.String(64), nullable=True),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("effective_date", sa.Date, nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "kind", "external_key", "revision"),
    )
    op.create_index("ix_market_events_announced", "market_events", ["announced_at"])
    op.create_index(
        "ix_market_events_symbol_announced", "market_events", ["symbol", "announced_at"]
    )

    op.create_table(
        "market_risk_flags",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("end_date", sa.Date, nullable=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "kind", "start_date", "source", "revision"),
    )
    op.create_index("ix_market_risk_flags_start", "market_risk_flags", ["start_date"])

    op.create_table(
        "monthly_revenue",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("revenue", sa.Float, nullable=False),
        sa.Column("mom_pct", sa.Float, nullable=True),
        sa.Column("yoy_pct", sa.Float, nullable=True),
        sa.Column("cumulative_revenue", sa.Float, nullable=True),
        sa.Column("cumulative_yoy_pct", sa.Float, nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "period", "revision"),
    )
    op.create_index("ix_monthly_revenue_period", "monthly_revenue", ["period"])


def downgrade():
    op.drop_index("ix_monthly_revenue_period", table_name="monthly_revenue")
    op.drop_table("monthly_revenue")
    op.drop_index("ix_market_risk_flags_start", table_name="market_risk_flags")
    op.drop_table("market_risk_flags")
    op.drop_index("ix_market_events_symbol_announced", table_name="market_events")
    op.drop_index("ix_market_events_announced", table_name="market_events")
    op.drop_table("market_events")
