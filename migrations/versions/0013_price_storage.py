"""Slim, partitioned minute bars and a one-row-per-symbol latest price.

* ``market_symbols``: symbol -> small integer id.
* ``minute_bars``: replaces ``quote_minutes``. Integer hundredths for prices, the
  minute's own volume, an ``official`` flag, partitioned by Taipei calendar month.
  Existing rows are copied over (volume = last_total - first_total).
* ``quote_latest``: newest price per symbol (live ticks or whole-market snapshots).
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

from quant_signal.infrastructure.db.partitions import create_partition_sql, months_between

revision = "0013_price_storage"
down_revision = "0012_market_industries"
branch_labels = None
depends_on = None

TAIPEI = ZoneInfo("Asia/Taipei")


def upgrade():
    op.create_table(
        "market_symbols",
        sa.Column("id", sa.SmallInteger, sa.Identity(), primary_key=True),
        sa.Column("symbol", sa.String(16), nullable=False, unique=True),
    )
    op.execute(
        """
        CREATE TABLE minute_bars (
            minute timestamptz NOT NULL,
            open integer NOT NULL,
            high integer NOT NULL,
            low integer NOT NULL,
            close integer NOT NULL,
            volume integer NOT NULL,
            symbol_id smallint NOT NULL,
            official boolean NOT NULL,
            PRIMARY KEY (symbol_id, minute)
        ) PARTITION BY RANGE (minute)
        """
    )
    bind = op.get_bind()
    earliest = bind.execute(sa.text("SELECT min(minute) FROM quote_minutes")).scalar()
    today = datetime.now(TAIPEI).date()
    first = earliest.astimezone(TAIPEI).date() if earliest else today
    for month in months_between(first, today + timedelta(days=62)):
        op.execute(create_partition_sql(month))
    op.execute(
        "INSERT INTO market_symbols (symbol) SELECT DISTINCT symbol FROM quote_minutes "
        "ORDER BY symbol"
    )
    op.execute(
        """
        INSERT INTO minute_bars (minute, open, high, low, close, volume, symbol_id, official)
        SELECT q.minute,
               round(q.open * 100)::integer, round(q.high * 100)::integer,
               round(q.low * 100)::integer, round(q.close * 100)::integer,
               greatest(0, q.last_total_volume - q.first_total_volume)::integer,
               s.id, NOT q.is_partial
        FROM quote_minutes q JOIN market_symbols s ON s.symbol = q.symbol
        """
    )
    op.drop_table("quote_minutes")
    op.create_table(
        "quote_latest",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("trade_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("volume", sa.BigInteger),
        sa.Column("amount", sa.BigInteger),
        sa.Column("price", sa.Integer, nullable=False),
        sa.Column("reference", sa.Integer),
        sa.Column("open", sa.Integer),
        sa.Column("high", sa.Integer),
        sa.Column("low", sa.Integer),
        sa.Column("source", sa.String(16), nullable=False),
    )


def downgrade():
    op.drop_table("quote_latest")
    op.create_table(
        "quote_minutes",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("minute", sa.DateTime(timezone=True), primary_key=True),
        *(sa.Column(name, sa.Float, nullable=False) for name in ("open", "high", "low", "close")),
        sa.Column("first_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_total_volume", sa.BigInteger, nullable=False),
        sa.Column("last_total_volume", sa.BigInteger, nullable=False),
        sa.Column("is_partial", sa.Boolean, nullable=False),
    )
    op.execute(
        """
        INSERT INTO quote_minutes
        SELECT s.symbol, b.minute, b.open / 100.0, b.high / 100.0, b.low / 100.0,
               b.close / 100.0, b.minute, b.minute + interval '59 seconds', 0, b.volume,
               NOT b.official
        FROM minute_bars b JOIN market_symbols s ON s.id = b.symbol_id
        """
    )
    op.execute("DROP TABLE minute_bars")
    op.drop_table("market_symbols")
