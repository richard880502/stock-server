"""Official daily valuation (P/E, P/B, dividend yield) and quarterly financial statements.

Chained after 0017 in this branch; the integrator re-chains it after the other data branches.
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_valuation_fundamentals"
down_revision = "0017_holders_data"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "valuation_daily",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("pe_ratio", sa.Float, nullable=True),
        sa.Column("pb_ratio", sa.Float, nullable=True),
        sa.Column("dividend_yield_pct", sa.Float, nullable=True),
        sa.Column("dividend_year", sa.Integer, nullable=True),
        sa.Column("fiscal_period", sa.String(8), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "trading_date", "source"),
    )
    op.create_index("ix_valuation_daily_date", "valuation_daily", ["trading_date"])

    op.create_table(
        "quarterly_financials",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("fiscal_year", sa.Integer, nullable=False),
        sa.Column("fiscal_quarter", sa.SmallInteger, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("statement_kind", sa.String(16), nullable=False),
        sa.Column("cumulative", sa.Boolean, nullable=False),
        sa.Column("revenue", sa.Float, nullable=True),
        sa.Column("gross_profit", sa.Float, nullable=True),
        sa.Column("operating_income", sa.Float, nullable=True),
        sa.Column("net_income_parent", sa.Float, nullable=True),
        sa.Column("eps", sa.Float, nullable=True),
        sa.Column("equity_parent", sa.Float, nullable=True),
        sa.Column("total_assets", sa.Float, nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "fiscal_year", "fiscal_quarter", "revision"),
    )
    op.create_index(
        "ix_quarterly_financials_period",
        "quarterly_financials",
        ["fiscal_year", "fiscal_quarter"],
    )


def downgrade():
    op.drop_index("ix_quarterly_financials_period", table_name="quarterly_financials")
    op.drop_table("quarterly_financials")
    op.drop_index("ix_valuation_daily_date", table_name="valuation_daily")
    op.drop_table("valuation_daily")
