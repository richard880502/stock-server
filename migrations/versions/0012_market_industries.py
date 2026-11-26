"""Add stock industry classification (產業別) for sector rotation."""

import sqlalchemy as sa
from alembic import op

revision = "0012_market_industries"
down_revision = "0011_quote_minutes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "market_industries",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("industry_code", sa.String(8), nullable=False),
        sa.Column("industry_name", sa.String(64), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("market_industries")
