"""Monthly partitions for ``minute_bars``.

Months follow Asia/Taipei, the market's calendar, so one trading day never spans two
partitions. Partitions are created ahead of time by the ingest service (and by the
migration), and old ones can be dropped wholesale for retention, which is far
cheaper than deleting rows.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
PARENT = "minute_bars"
_NAME = re.compile(r"^minute_bars_(\d{4})_(\d{2})$")


def month_start(day: date) -> date:
    return day.replace(day=1)


def add_months(month: date, count: int) -> date:
    index = month.year * 12 + month.month - 1 + count
    return date(index // 12, index % 12 + 1, 1)


def partition_name(month: date) -> str:
    return f"{PARENT}_{month.year:04d}_{month.month:02d}"


def partition_month(name: str) -> date | None:
    match = _NAME.fullmatch(name)
    return date(int(match[1]), int(match[2]), 1) if match else None


def _bound(month: date) -> str:
    return datetime(month.year, month.month, 1, tzinfo=TAIPEI).isoformat()


def create_partition_sql(month: date) -> str:
    """Idempotent DDL for the partition holding ``month`` (Taipei calendar month)."""
    month = month_start(month)
    return (
        f"CREATE TABLE IF NOT EXISTS {partition_name(month)} PARTITION OF {PARENT} "
        f"FOR VALUES FROM ('{_bound(month)}') TO ('{_bound(add_months(month, 1))}')"
    )


def months_between(first: date, last: date) -> list[date]:
    months, current = [], month_start(first)
    while current <= month_start(last):
        months.append(current)
        current = add_months(current, 1)
    return months


def expired_partitions(names: list[str], today: date, keep_months: int) -> list[str]:
    """Partitions entirely older than the last ``keep_months`` months (0 keeps all)."""
    if keep_months <= 0:
        return []
    oldest_kept = add_months(month_start(today), -(keep_months - 1))
    return sorted(
        name
        for name in names
        if (month := partition_month(name)) is not None and month < oldest_kept
    )
