"""Securities-lending short selling, day trading, insider pledges and dividend events.

Branch-local chain: follows 0017; the integrator re-chains this revision.
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_short_daytrade_events"
down_revision = "0019_valuation_fundamentals"
branch_labels = None
depends_on = None

AT = sa.DateTime(timezone=True)


def upgrade():
    op.create_table(
        "short_lending_daily",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("revision", sa.SmallInteger, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("sbl_prev_balance", sa.BigInteger),
        sa.Column("sbl_sold", sa.BigInteger),
        sa.Column("sbl_returned", sa.BigInteger),
        sa.Column("sbl_adjustment", sa.BigInteger),
        sa.Column("sbl_balance", sa.BigInteger),
        sa.Column("sbl_limit_next_day", sa.BigInteger),
        sa.Column("available_at", AT, nullable=False),
        sa.PrimaryKeyConstraint("symbol", "trading_date", "revision"),
    )
    op.create_index("ix_short_lending_date", "short_lending_daily", ["trading_date"])

    op.create_table(
        "day_trade_daily",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("revision", sa.SmallInteger, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("day_trade_volume", sa.BigInteger),
        sa.Column("day_trade_buy_amount", sa.BigInteger),
        sa.Column("day_trade_sell_amount", sa.BigInteger),
        sa.Column("available_at", AT, nullable=False),
        sa.PrimaryKeyConstraint("symbol", "trading_date", "revision"),
    )
    op.create_index("ix_day_trade_date", "day_trade_daily", ["trading_date"])

    op.create_table(
        "insider_holdings_monthly",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("revision", sa.SmallInteger, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("insider_count", sa.Integer, nullable=False),
        sa.Column("holding_shares", sa.BigInteger, nullable=False),
        sa.Column("pledged_shares", sa.BigInteger, nullable=False),
        sa.Column("pledge_ratio_pct", sa.Float),
        sa.Column("published_on", sa.Date),
        sa.Column("available_at", AT, nullable=False),
        sa.PrimaryKeyConstraint("symbol", "period", "revision"),
    )

    op.create_table(
        "dividend_events",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("ex_date", sa.Date, nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("revision", sa.SmallInteger, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("cash_dividend_per_share", sa.Float),
        sa.Column("stock_dividend_per_share", sa.Float),
        sa.Column("cash_capital_increase_ratio", sa.Float),
        sa.Column("subscription_price", sa.Float),
        sa.Column("available_at", AT, nullable=False),
        sa.PrimaryKeyConstraint("symbol", "ex_date", "kind", "revision"),
    )
    op.create_index("ix_dividend_events_ex_date", "dividend_events", ["ex_date"])


def downgrade():
    op.drop_index("ix_dividend_events_ex_date", table_name="dividend_events")
    op.drop_table("dividend_events")
    op.drop_table("insider_holdings_monthly")
    op.drop_index("ix_day_trade_date", table_name="day_trade_daily")
    op.drop_table("day_trade_daily")
    op.drop_index("ix_short_lending_date", table_name="short_lending_daily")
    op.drop_table("short_lending_daily")
