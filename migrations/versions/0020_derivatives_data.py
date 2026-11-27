"""TAIFEX derivatives: institutional futures/options positions, P/C ratio, large traders,
single-stock futures, TXO open-interest strikes and VIX.

Chained after 0017 in this branch; the integrator re-chains it behind the other data branches.
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_derivatives_data"
down_revision = "0017_holders_data"
branch_labels = None
depends_on = None


def _provenance() -> list[sa.Column]:
    return [
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _big(name: str) -> sa.Column:
    return sa.Column(name, sa.BigInteger, nullable=False)


def upgrade():
    op.create_table(
        "futures_institutional_daily",
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("contract", sa.String(8), nullable=False),
        sa.Column("investor", sa.String(8), nullable=False),
        _big("long_trade_contracts"),
        _big("long_trade_value_ntd_k"),
        _big("short_trade_contracts"),
        _big("short_trade_value_ntd_k"),
        _big("long_oi_contracts"),
        _big("long_oi_value_ntd_k"),
        _big("short_oi_contracts"),
        _big("short_oi_value_ntd_k"),
        *_provenance(),
        sa.PrimaryKeyConstraint("trading_date", "contract", "investor"),
    )
    op.create_table(
        "options_pc_ratio_daily",
        sa.Column("trading_date", sa.Date, nullable=False),
        _big("put_volume_contracts"),
        _big("call_volume_contracts"),
        sa.Column("volume_ratio_pct", sa.Float, nullable=False),
        _big("put_oi_contracts"),
        _big("call_oi_contracts"),
        sa.Column("oi_ratio_pct", sa.Float, nullable=False),
        *_provenance(),
        sa.PrimaryKeyConstraint("trading_date"),
    )
    op.create_table(
        "options_institutional_daily",
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("contract", sa.String(8), nullable=False),
        sa.Column("call_put", sa.String(4), nullable=False),
        sa.Column("investor", sa.String(8), nullable=False),
        _big("buy_trade_contracts"),
        _big("buy_trade_value_ntd_k"),
        _big("sell_trade_contracts"),
        _big("sell_trade_value_ntd_k"),
        _big("buy_oi_contracts"),
        _big("buy_oi_value_ntd_k"),
        _big("sell_oi_contracts"),
        _big("sell_oi_value_ntd_k"),
        *_provenance(),
        sa.PrimaryKeyConstraint("trading_date", "contract", "call_put", "investor"),
    )
    op.create_table(
        "futures_large_traders_daily",
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("contract", sa.String(8), nullable=False),
        sa.Column("trader_type", sa.String(8), nullable=False),
        _big("top5_long_contracts"),
        _big("top5_short_contracts"),
        _big("top10_long_contracts"),
        _big("top10_short_contracts"),
        _big("market_oi_contracts"),
        *_provenance(),
        sa.PrimaryKeyConstraint("trading_date", "contract", "trader_type"),
    )
    op.create_table(
        "stock_futures_daily",
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("underlying_code", sa.String(16), nullable=False),
        sa.Column("total_open_interest", sa.Float, nullable=False),
        sa.Column("total_volume", sa.Float, nullable=False),
        _big("open_interest_shares"),
        _big("volume_shares"),
        sa.Column("near_month", sa.String(8)),
        sa.Column("near_settlement", sa.Float),
        sa.Column("near_volume", sa.Float),
        sa.Column("near_open_interest", sa.Float),
        _big("product_count"),
        *_provenance(),
        sa.PrimaryKeyConstraint("symbol", "trading_date"),
    )
    op.create_index("ix_stock_futures_date", "stock_futures_daily", ["trading_date"])
    op.create_table(
        "options_oi_levels_daily",
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("expiry", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("expiry_date", sa.Date, nullable=False),
        sa.Column("max_call_strike", sa.Float),
        sa.Column("max_call_oi_contracts", sa.BigInteger),
        sa.Column("max_put_strike", sa.Float),
        sa.Column("max_put_oi_contracts", sa.BigInteger),
        _big("call_total_oi_contracts"),
        _big("put_total_oi_contracts"),
        *_provenance(),
        sa.PrimaryKeyConstraint("trading_date", "expiry"),
    )
    op.create_table(
        "vix_daily",
        sa.Column("trading_date", sa.Date, nullable=False),
        sa.Column("vix_close", sa.Float, nullable=False),
        sa.Column("vix_last_minute_avg", sa.Float),
        *_provenance(),
        sa.PrimaryKeyConstraint("trading_date"),
    )


def downgrade():
    op.drop_index("ix_stock_futures_date", table_name="stock_futures_daily")
    for table in (
        "vix_daily",
        "options_oi_levels_daily",
        "stock_futures_daily",
        "futures_large_traders_daily",
        "options_institutional_daily",
        "options_pc_ratio_daily",
        "futures_institutional_daily",
    ):
        op.drop_table(table)
