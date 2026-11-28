"""Paper trading: dividends (paper_corporate_actions), the ledger's link to them, and
the events evaluated with nothing due (paper_dividend_checks)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_paper_corporate_actions"
down_revision: str | None = "0023_paper_trading"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "paper_corporate_actions",
        sa.Column("id", UUID, nullable=False),
        sa.Column("account_id", UUID, nullable=False),
        sa.Column("epoch", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("ex_date", sa.Date, nullable=False),
        sa.Column("event_kind", sa.String(20), nullable=False),
        sa.Column("entitled_shares", sa.Integer, nullable=False),
        sa.Column("cash_dividend_per_share", sa.Numeric(14, 8), nullable=True),
        sa.Column("stock_dividend_per_share", sa.Numeric(14, 8), nullable=True),
        sa.Column("cash_amount", sa.BigInteger, server_default=sa.text("0"), nullable=False),
        sa.Column("shares_added", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("ex_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_revision", sa.Integer, nullable=False),
        sa.Column("source_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revalued_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "epoch", "symbol", "ex_date", name="uq_paper_actions_event"
        ),
        sa.CheckConstraint("entitled_shares > 0", name="ck_paper_actions_entitled"),
        sa.CheckConstraint(
            "cash_amount >= 0 AND shares_added >= 0 AND (cash_amount > 0 OR shares_added > 0)",
            name="ck_paper_actions_payout",
        ),
    )
    op.create_index(
        "ix_paper_actions_account_epoch",
        "paper_corporate_actions",
        ["account_id", "epoch", "ex_at"],
    )

    op.create_table(
        "paper_dividend_checks",
        sa.Column("account_id", UUID, nullable=False),
        sa.Column("epoch", sa.Integer, nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("ex_date", sa.Date, nullable=False),
        sa.Column("source_revision", sa.Integer, nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["paper_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("account_id", "epoch", "symbol", "ex_date"),
    )

    op.add_column("paper_cash_ledger", sa.Column("action_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_paper_ledger_action",
        "paper_cash_ledger",
        "paper_corporate_actions",
        ["action_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_paper_ledger_action_kind", "paper_cash_ledger", ["action_id", "kind"]
    )
    op.create_check_constraint(
        "ck_paper_ledger_action",
        "paper_cash_ledger",
        "(kind = 'dividend') = (action_id IS NOT NULL)",
    )


def downgrade() -> None:
    # Dividend postings only make sense with their actions; drop them with the link.
    op.execute("DELETE FROM paper_cash_ledger WHERE kind = 'dividend'")
    op.drop_constraint("ck_paper_ledger_action", "paper_cash_ledger", type_="check")
    op.drop_constraint("uq_paper_ledger_action_kind", "paper_cash_ledger", type_="unique")
    op.drop_constraint("fk_paper_ledger_action", "paper_cash_ledger", type_="foreignkey")
    op.drop_column("paper_cash_ledger", "action_id")
    op.drop_table("paper_dividend_checks")
    op.drop_index("ix_paper_actions_account_epoch", table_name="paper_corporate_actions")
    op.drop_table("paper_corporate_actions")
