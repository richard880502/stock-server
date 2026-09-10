"""Add point-in-time market breadth and capital environment tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_market_environment"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_observations_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("index_close", sa.Float(), nullable=False),
        sa.Column("total_issues", sa.Integer(), nullable=False),
        sa.Column("advancing_issues", sa.Integer(), nullable=False),
        sa.Column("declining_issues", sa.Integer(), nullable=False),
        sa.Column("unchanged_issues", sa.Integer(), nullable=False),
        sa.Column("above_ma20_issues", sa.Integer(), nullable=False),
        sa.Column("above_ma60_issues", sa.Integer(), nullable=False),
        sa.Column("new_high_52w_issues", sa.Integer(), nullable=False),
        sa.Column("new_low_52w_issues", sa.Integer(), nullable=False),
        sa.Column("up_volume", sa.Float(), nullable=False),
        sa.Column("down_volume", sa.Float(), nullable=False),
        sa.Column("turnover_value", sa.Float(), nullable=False),
        sa.Column("foreign_net_flow", sa.Float(), nullable=False),
        sa.Column("investment_trust_net_flow", sa.Float(), nullable=False),
        sa.Column("dealer_net_flow", sa.Float(), nullable=False),
        sa.Column("futures_net_open_interest", sa.Float(), nullable=False),
        sa.Column("margin_balance", sa.Float(), nullable=False),
        sa.Column("short_balance", sa.Float(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "market",
            "trading_date",
            "source",
            "revision",
            name="uq_market_observation_version",
        ),
    )
    op.create_index(
        "ix_market_observation_market_date",
        "market_observations_daily",
        ["market", "trading_date"],
    )
    op.create_table(
        "market_environment_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("market", sa.String(length=32), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("regime", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "market",
            "as_of",
            "strategy_version",
            name="uq_market_environment_snapshot",
        ),
    )


def downgrade() -> None:
    op.drop_table("market_environment_snapshots")
    op.drop_index(
        "ix_market_observation_market_date",
        table_name="market_observations_daily",
    )
    op.drop_table("market_observations_daily")
