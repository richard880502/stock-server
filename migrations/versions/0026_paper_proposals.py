"""AI trade proposals for paper trading (AI 提案)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_paper_proposals"
down_revision: str | None = "0024_paper_corporate_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "paper_proposals",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("account_id", UUID, nullable=False),
        sa.Column("epoch", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("as_of", sa.Date, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("action", sa.String(4), nullable=False),
        sa.Column("side", sa.String(4), nullable=True),
        sa.Column("limit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("lots", sa.Integer, nullable=True),
        sa.Column("order_id", UUID, nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.Text, nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["paper_orders.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending','accepted','rejected','expired','superseded')",
            name="ck_paper_proposals_status",
        ),
        sa.CheckConstraint("action IN ('buy','sell','hold')", name="ck_paper_proposals_action"),
    )
    op.create_index(
        "ix_paper_proposals_account_created", "paper_proposals", ["account_id", "created_at"]
    )
    op.create_index(
        "uq_paper_proposals_pending",
        "paper_proposals",
        ["account_id", "symbol"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "uq_paper_proposals_order",
        "paper_proposals",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("order_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("paper_proposals")
