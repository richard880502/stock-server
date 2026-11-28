"""Deposits and withdrawals on paper accounts (入金／出金).

Transfers are ledger postings (``transfer_in`` / ``transfer_out``) keyed by the client's
``request_id``. Snapshots store no flow: readers derive it from the ledger. Downgrading
deletes every transfer posting (see ``downgrade``)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_paper_transfers"
down_revision: str | None = "0026_paper_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("paper_cash_ledger", sa.Column("request_id", postgresql.UUID(as_uuid=True)))
    op.create_unique_constraint(
        "uq_paper_ledger_request", "paper_cash_ledger", ["account_id", "request_id"]
    )
    op.create_check_constraint(
        "ck_paper_ledger_request",
        "paper_cash_ledger",
        "(kind IN ('transfer_in','transfer_out')) = (request_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_paper_ledger_transfer_sign",
        "paper_cash_ledger",
        "(kind <> 'transfer_in' OR amount > 0) AND (kind <> 'transfer_out' OR amount < 0)",
    )


def downgrade() -> None:
    # Older code can neither parse these postings (``LedgerEntry.kind``) nor tell them from
    # trading cash, so they go with the column that marks them: downgrading DELETES every
    # deposit / withdrawal, and the affected accounts' cash drops (or rises) by their sum.
    op.execute("DELETE FROM paper_cash_ledger WHERE kind IN ('transfer_in', 'transfer_out')")
    op.drop_constraint("ck_paper_ledger_transfer_sign", "paper_cash_ledger", type_="check")
    op.drop_constraint("ck_paper_ledger_request", "paper_cash_ledger", type_="check")
    op.drop_constraint("uq_paper_ledger_request", "paper_cash_ledger", type_="unique")
    op.drop_column("paper_cash_ledger", "request_id")
