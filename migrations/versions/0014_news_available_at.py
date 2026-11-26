"""news_items.available_at: when the system first held a headline.

``published_at`` is the publisher's clock; an item published on 11/20 but fetched on
11/26 was not knowable on 11/21, so point-in-time reads must filter on
``available_at``. Existing rows are backfilled from ``retrieved_at``.
``classified_at`` records when the sentiment label was written, so an as-of read
before that moment sees the item as unclassified.
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_news_available_at"
down_revision = "0013_price_storage"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "news_items", sa.Column("available_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "news_items", sa.Column("classified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE news_items SET available_at = retrieved_at")
    op.execute(
        "UPDATE news_items SET classified_at = retrieved_at WHERE sentiment_label IS NOT NULL"
    )
    op.alter_column("news_items", "available_at", nullable=False)
    op.create_index(
        "ix_news_item_instrument_available", "news_items", ["instrument_id", "available_at"]
    )


def downgrade():
    op.drop_index("ix_news_item_instrument_available", table_name="news_items")
    op.drop_column("news_items", "classified_at")
    op.drop_column("news_items", "available_at")
