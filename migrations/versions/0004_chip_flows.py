"""Add stock-level institutional and licensed broker-branch flows."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_chip_flows"
down_revision: str | None = "0003_adjusted_prices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "institutional_flows_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("foreign_buy", sa.BigInteger(), nullable=False),
        sa.Column("foreign_sell", sa.BigInteger(), nullable=False),
        sa.Column("foreign_net", sa.BigInteger(), nullable=False),
        sa.Column("investment_trust_buy", sa.BigInteger(), nullable=False),
        sa.Column("investment_trust_sell", sa.BigInteger(), nullable=False),
        sa.Column("investment_trust_net", sa.BigInteger(), nullable=False),
        sa.Column("dealer_buy", sa.BigInteger(), nullable=False),
        sa.Column("dealer_sell", sa.BigInteger(), nullable=False),
        sa.Column("dealer_net", sa.BigInteger(), nullable=False),
        sa.Column("total_net", sa.BigInteger(), nullable=False),
        sa.Column("foreign_holding_ratio", sa.Float(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instrument_id",
            "trading_date",
            "source",
            "revision",
            name="uq_institutional_flow_version",
        ),
    )
    op.create_index(
        "ix_institutional_flow_instrument_date",
        "institutional_flows_daily",
        ["instrument_id", "trading_date"],
    )
    op.create_table(
        "broker_branch_flows_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("branch_code", sa.String(length=32), nullable=False),
        sa.Column("branch_name", sa.String(length=255), nullable=False),
        sa.Column("buy_shares", sa.BigInteger(), nullable=False),
        sa.Column("sell_shares", sa.BigInteger(), nullable=False),
        sa.Column("buy_amount", sa.Float(), nullable=False),
        sa.Column("sell_amount", sa.Float(), nullable=False),
        sa.Column("day_trade_buy_shares", sa.BigInteger(), nullable=True),
        sa.Column("day_trade_sell_shares", sa.BigInteger(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["instrument_id"], ["instruments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "instrument_id",
            "trading_date",
            "branch_code",
            "source",
            "revision",
            name="uq_broker_branch_flow_version",
        ),
    )
    op.create_index(
        "ix_broker_branch_flow_instrument_date",
        "broker_branch_flows_daily",
        ["instrument_id", "trading_date"],
    )
    op.create_table(
        "chip_flow_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
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
            "symbol",
            "as_of",
            "strategy_version",
            name="uq_chip_flow_snapshot",
        ),
    )


def downgrade() -> None:
    op.drop_table("chip_flow_snapshots")
    op.drop_index(
        "ix_broker_branch_flow_instrument_date",
        table_name="broker_branch_flows_daily",
    )
    op.drop_table("broker_branch_flows_daily")
    op.drop_index(
        "ix_institutional_flow_instrument_date",
        table_name="institutional_flows_daily",
    )
    op.drop_table("institutional_flows_daily")
