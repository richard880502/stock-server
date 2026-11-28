"""Re-stamp rows of past trading days that were stamped with their (late) fetch time.

The live windows of the valuation, 借券, 當沖 and foreign-holding feeds used to stamp
``available_at`` with the fetch time. A row for trading date D fetched days later (the first
deploy on a weekend) then became "visible" only after the fetch, so an as-of read for D saw
the previous day. The rule is now ``available_at = min(fetch time, publication time of D)``;
this migration applies it to rows already stored. Publication times (Asia/Taipei):

- valuation_daily: 18:00 on D
- short_lending_daily: 22:35 on D
- day_trade_daily: 23:00 on D + 2 weekdays
- foreign_holding_daily: 21:00 on D

Only ever lowers a stamp that is later than the rule allows, so it is idempotent and rows
fetched on time are untouched. For the two revisioned tables only revision 1 (the first
publication) is touched; a later revision is a genuine later change.

The downgrade is a no-op: the original fetch times were not kept, and the old stamps were
the defect (they would make as-of reads wrong again).
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_restamp_late_fetched_rows"
down_revision = "0021_short_daytrade_events"
branch_labels = None
depends_on = None

TZ = "Asia/Taipei"

# (table, publication moment as SQL, extra WHERE)
TARGETS = (
    ("valuation_daily", f"(trading_date + time '18:00') AT TIME ZONE '{TZ}'", ""),
    ("foreign_holding_daily", f"(trading_date + time '21:00') AT TIME ZONE '{TZ}'", ""),
    (
        "short_lending_daily",
        f"(trading_date + time '22:35') AT TIME ZONE '{TZ}'",
        "AND revision = 1",
    ),
    (
        "day_trade_daily",
        # T+2 weekdays: Mon-Wed -> +2 days, Thu/Fri -> +4, Sat -> +3, Sun -> +2
        "((trading_date + CASE EXTRACT(ISODOW FROM trading_date)::int "
        "WHEN 4 THEN 4 WHEN 5 THEN 4 WHEN 6 THEN 3 ELSE 2 END) + time '23:00') "
        f"AT TIME ZONE '{TZ}'",
        "AND revision = 1",
    ),
)


def restamp(connection) -> dict[str, int]:
    """Apply the rule to the stored rows; returns the number of rows changed per table."""
    changed: dict[str, int] = {}
    for table, published, extra in TARGETS:
        result = connection.execute(
            sa.text(
                f"UPDATE {table} SET available_at = {published} "  # noqa: S608 - constants
                f"WHERE available_at > {published} {extra}"
            )
        )
        changed[table] = result.rowcount
    return changed


def upgrade():
    restamp(op.get_bind())


def downgrade():
    # Intentionally empty: see the module docstring.
    pass
