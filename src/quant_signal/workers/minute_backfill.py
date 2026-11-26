"""Fill gaps in ``quote_minutes`` from Shioaji's official 1-minute bars.

Two moments use it:

* Intraday, the first time a symbol is subscribed: the minutes before the live feed
  started are filled once (only whole minutes before the subscription, so the live
  feed and the backfill never write the same minute).
* After the close, once per trading day: every symbol anyone watches is replaced
  with the official bars, so a day is complete even if nobody looked at it live or
  the collector was disconnected.

Shioaji documents kbars as an after-the-fact source with a shared query budget
(50 per 10 s, 270 kbars queries during the session), so calls are paced and the
intraday ones are capped per day.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Callable, Iterable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from quant_signal.infrastructure.db.database import session_factory
from quant_signal.infrastructure.db.models import QuoteMinuteRow, WatchlistItemRow

log = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
SESSION_OPEN = time(9, 0)
# Shared with snapshots/ticks: 50 queries per 10 s. Stay well under it.
QUERY_INTERVAL_SECONDS = 0.35


def taipei_now() -> datetime:
    return datetime.now(TAIPEI)


def post_close_due(now: datetime, at: time) -> bool:
    """True on a weekday once the configured after-close time has passed (Taipei)."""
    local = now.astimezone(TAIPEI)
    return local.weekday() < 5 and local.time() >= at


def parse_clock(value: str) -> time:
    hour, minute = value.strip().split(":")
    return time(int(hour), int(minute))


def alignment(existing: Iterable[dict], official: Iterable[dict]) -> tuple[int, int]:
    """How many overlapping minutes have the same close (live vs official).

    A low match rate means the kbars label/timezone reading is off; the caller logs it.
    Only finished live minutes are compared.
    """
    official_close = {row["minute"]: row["close"] for row in official}
    compared = matched = 0
    for row in existing:
        close = official_close.get(row["minute"])
        if close is None:
            continue
        compared += 1
        if abs(close - row["close"]) < 1e-6:
            matched += 1
    return compared, matched


async def store_official_minutes(rows: list[dict], *, replace: bool) -> int:
    """Write official minutes. ``replace`` overwrites live rows (after the close only)."""
    if not rows:
        return 0
    statement = insert(QuoteMinuteRow).values(rows)
    if replace:
        incoming = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=["symbol", "minute"],
            set_={
                column: getattr(incoming, column)
                for column in (
                    "open",
                    "high",
                    "low",
                    "close",
                    "first_time",
                    "last_time",
                    "first_total_volume",
                    "last_total_volume",
                    "is_partial",
                )
            },
        )
    else:
        statement = statement.on_conflict_do_nothing(index_elements=["symbol", "minute"])
    async with session_factory() as session:
        await session.execute(statement)
        await session.commit()
    return len(rows)


async def live_minutes(symbol: str, day: date) -> list[dict]:
    start = datetime.combine(day, time(0), TAIPEI)
    statement = select(QuoteMinuteRow).where(
        QuoteMinuteRow.symbol == symbol,
        QuoteMinuteRow.minute >= start,
        QuoteMinuteRow.minute < start + timedelta(days=1),
        QuoteMinuteRow.is_partial.is_(True),
    )
    async with session_factory() as session:
        rows = (await session.scalars(statement)).all()
    return [{"minute": row.minute, "close": row.close} for row in rows]


async def symbols_to_complete(day: date, extra: Iterable[str] = ()) -> list[str]:
    """Everything worth a full day: watchlists, anything seen live today, current demand."""
    start = datetime.combine(day, time(0), TAIPEI)
    async with session_factory() as session:
        watched = (await session.scalars(select(WatchlistItemRow.symbol).distinct())).all()
        seen = (
            await session.scalars(
                select(QuoteMinuteRow.symbol)
                .where(
                    QuoteMinuteRow.minute >= start,
                    QuoteMinuteRow.minute < start + timedelta(days=1),
                )
                .distinct()
            )
        ).all()
    return sorted({s.upper() for s in (*watched, *seen, *extra)})


async def backfill(
    fetch: Callable[[str, date], list[dict]],
    symbols: Iterable[str],
    day: date,
    *,
    replace: bool,
    before: datetime | None = None,
    pause: float = QUERY_INTERVAL_SECONDS,
) -> dict[str, int]:
    """Fetch and store official minutes for each symbol; returns rows written per symbol.

    ``fetch`` is the blocking SDK call (run in a thread). ``before`` keeps only minutes
    that started before it (intraday: the subscription time).
    """
    written: dict[str, int] = {}
    for index, symbol in enumerate(symbols):
        if index:
            await asyncio.sleep(pause)
        try:
            rows = await asyncio.to_thread(fetch, symbol, day)
        except (RuntimeError, ValueError, LookupError):
            # SDK messages can contain account data; keep logs generic.
            log.warning("Minute backfill skipped one symbol")
            continue
        if before is not None:
            cutoff = before.replace(second=0, microsecond=0)
            rows = [row for row in rows if row["minute"] < cutoff]
        if replace and rows:
            compared, matched = alignment(await live_minutes(symbol, day), rows)
            if compared >= 10 and matched / compared < 0.8:
                log.warning(
                    "Official minutes disagree with live minutes for %s: %d/%d closes match",
                    symbol,
                    matched,
                    compared,
                )
        written[symbol] = await store_official_minutes(rows, replace=replace)
    return written


def main() -> None:
    """Manual catch-up: ``quant-signal-backfill-minutes --date 2026-11-26 [--symbol 2330.TW]``.

    Opens its own Shioaji connection (each account allows up to 5).
    """
    from quant_signal.infrastructure.providers.shioaji_feed import ShioajiFeed
    from quant_signal.settings import get_settings

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--date", type=date.fromisoformat, default=taipei_now().date())
    parser.add_argument("--symbol", action="append", default=[])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    async def run() -> None:
        feed = ShioajiFeed(get_settings(), lambda tick: None, lambda *a: None)
        await asyncio.to_thread(feed.start)
        try:
            symbols = args.symbol or await symbols_to_complete(args.date)
            written = await backfill(feed.minute_bars, symbols, args.date, replace=True)
            log.info("Backfilled %d symbols, %d minutes", len(written), sum(written.values()))
        finally:
            await asyncio.to_thread(feed.close)

    asyncio.run(run())
