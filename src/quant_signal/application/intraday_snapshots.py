"""Write path for the 10-minute intraday market snapshots.

The quote collector (the only component allowed to talk to Shioaji) calls
``store_intraday_snapshots`` after each tick. Row contract (``IntradaySnapshot``):

- ``symbol``: ``"2330.TW"`` / ``"6488.TWO"``, the same form as ``market_stock_daily``.
- ``taken_at``: tz-aware tick time, floored to the 10-minute slot (09:10, 09:20, ...
  Taipei); identical for every symbol of one tick. Primary key is (symbol, taken_at),
  so a retried tick overwrites instead of duplicating.
- ``price``: last traded price (> 0).
- ``change_pct``: RATIO versus the previous close (0.0123 = +1.23%, not percent).
- ``volume_lots``: cumulative session volume so far, in lots (張, 1000 shares).
- ``amount_twd``: cumulative session turnover so far, in TWD.
- ``available_at``: when the collector actually received the quote (tz-aware). Readers
  never use a snapshot before this instant.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

from quant_signal.domain.models import IntradaySnapshot

SLOT_MINUTES = 10


class IntradayRepository(Protocol):
    async def upsert_intraday_snapshots(self, rows: list[IntradaySnapshot]) -> int: ...

    async def delete_intraday_snapshots_before(self, cutoff: datetime) -> int: ...


def floor_to_slot(moment: datetime) -> datetime:
    """Floor to the 10-minute slot (Taipei has a whole-hour UTC offset)."""
    minute = moment.minute - moment.minute % SLOT_MINUTES
    return moment.replace(minute=minute, second=0, microsecond=0)


async def store_intraday_snapshots(
    rows: Sequence[IntradaySnapshot],
    repository: IntradayRepository | None = None,
) -> int:
    """Upsert one tick's snapshots; the last row per (symbol, taken_at) wins.

    Without ``repository`` a database session is opened for the call.
    """
    unique = {(row.symbol, row.taken_at): row for row in rows}
    batch = list(unique.values())
    if not batch:
        return 0
    if repository is not None:
        return await repository.upsert_intraday_snapshots(batch)
    from quant_signal.infrastructure.db import PostgresQuantRepository, session_factory

    async with session_factory() as session:
        return await PostgresQuantRepository(session).upsert_intraday_snapshots(batch)


async def prune_intraday_snapshots(
    repository: IntradayRepository, *, keep_days: int = 30, now: datetime | None = None
) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=keep_days)
    return await repository.delete_intraday_snapshots_before(cutoff)
