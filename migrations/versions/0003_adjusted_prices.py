"""Add forward corporate-action adjustment factor to daily bars."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_adjusted_prices"
down_revision: str | None = "0002_market_environment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bars_daily",
        sa.Column(
            "adjustment_factor",
            sa.Float(),
            server_default=sa.text("1.0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("bars_daily", "adjustment_factor")
