"""Fill gaps in ``quote_minutes`` from Shioaji's official 1-minute bars (kbars).

Two moments use it, both inside the collector (the only process that talks to
Shioaji):

* Intraday, the first time a symbol is subscribed in a session: the minutes before
  the live feed started are filled once (only whole minutes strictly before the
  subscription minute, so the live feed and the backfill never write the same
  minute, and existing rows are never touched).
* After the close, once per weekday: every symbol anyone watches is replaced with
  the official bars, so a day is complete even if nobody looked at it live or the
  collector was disconnected.

Every kbars call goes through the shared Redis budget (``shioaji_budget``) and all
history queries run one at a time from a single queue.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from quant_signal.application.quote_cache import PREFIX, SYMBOL, is_futures
from quant_signal.infrastructure.db.database import session_factory
from quant_signal.infrastructure.db.models import QuoteMinuteRow, WatchlistItemRow
from quant_signal.infrastructure.providers.shioaji_budget import BudgetExhausted

log = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
STOCK_OPEN = time(9, 0)
FUTURES_DAY_OPEN = time(8, 45)
FUTURES_NIGHT_OPEN = time(15, 0)
# The budget enforces Shioaji's rate; this only spreads calls out a little.
QUERY_INTERVAL_SECONDS = 0.3
KEY = PREFIX + "backfill:"
DEDUPE_TTL_SECONDS = 2 * 86400
CLAIM_TTL_SECONDS = 3600  # A crashed after-close run is retried after this at most.
RETRY_AFTER_BUDGET_SECONDS = 900
UPDATED_COLUMNS = (
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

Fetch = Callable[[str, datetime, datetime], Awaitable[list[dict]]]


def taipei_now() -> datetime:
    return datetime.now(TAIPEI)


def parse_clock(value: str) -> time:
    hour, minute = value.strip().split(":")
    return time(int(hour), int(minute))


def post_close_due(now: datetime, at: time) -> bool:
    """True on a weekday once the configured after-close time has passed (Taipei)."""
    local = now.astimezone(TAIPEI)
    return local.weekday() < 5 and local.time() >= at


def previous_weekday(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def official_window(symbol: str, day: date) -> tuple[datetime, datetime]:
    """The minutes that make up trading day ``day`` for ``symbol``.

    Stocks: the calendar day. TAIEX futures: the night session that opened at 15:00
    on the previous weekday, then the day session up to 13:45 (TAIFEX counts the
    night session toward the next trading day).
    """
    if is_futures(symbol):
        start = datetime.combine(previous_weekday(day), FUTURES_NIGHT_OPEN, TAIPEI)
        return start, datetime.combine(day, time(14, 0), TAIPEI)
    start = datetime.combine(day, time(0), TAIPEI)
    return start, start + timedelta(days=1)


def intraday_window(symbol: str, subscribed_at: datetime) -> tuple[datetime, datetime] | None:
    """Session minutes before the subscription minute, or None when there are none.

    The end is the subscription minute itself (exclusive): the live feed owns it.
    Futures sessions are 08:45-13:45 and 15:00-05:00, stocks 09:00-13:30.
    """
    local = subscribed_at.astimezone(TAIPEI)
    cutoff = local.replace(second=0, microsecond=0)
    if is_futures(symbol):
        clock = local.time()
        if clock >= FUTURES_NIGHT_OPEN:
            start = datetime.combine(local.date(), FUTURES_NIGHT_OPEN, TAIPEI)
        elif clock >= FUTURES_DAY_OPEN:
            start = datetime.combine(local.date(), FUTURES_DAY_OPEN, TAIPEI)
        else:
            start = datetime.combine(local.date() - timedelta(days=1), FUTURES_NIGHT_OPEN, TAIPEI)
    else:
        start = datetime.combine(local.date(), STOCK_OPEN, TAIPEI)
    if start.weekday() >= 5 or cutoff <= start:
        return None
    return start, cutoff


def alignment(existing: Iterable[dict], official: Iterable[dict]) -> tuple[int, int]:
    """How many overlapping minutes have the same close (live vs official).

    A low match rate means the kbars label/timezone reading is off; the caller logs it.
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


def official_minutes_statement(rows: list[dict], *, replace: bool) -> Any:
    """Insert official minutes. ``replace`` overwrites live rows (after the close only)."""
    statement = insert(QuoteMinuteRow).values(rows)
    if not replace:
        return statement.on_conflict_do_nothing(index_elements=["symbol", "minute"])
    incoming = statement.excluded
    return statement.on_conflict_do_update(
        index_elements=["symbol", "minute"],
        set_={column: getattr(incoming, column) for column in UPDATED_COLUMNS},
    )


async def store_official_minutes(rows: list[dict], *, replace: bool) -> int:
    if not rows:
        return 0
    async with session_factory() as session:
        for index in range(0, len(rows), 500):
            await session.execute(
                official_minutes_statement(rows[index : index + 500], replace=replace)
            )
        await session.commit()
    return len(rows)


async def live_minutes(symbol: str, start: datetime, end: datetime) -> list[dict]:
    statement = select(QuoteMinuteRow).where(
        QuoteMinuteRow.symbol == symbol,
        QuoteMinuteRow.minute >= start,
        QuoteMinuteRow.minute < end,
        QuoteMinuteRow.is_partial.is_(True),
    )
    async with session_factory() as session:
        rows = (await session.scalars(statement)).all()
    return [{"minute": row.minute, "close": row.close} for row in rows]


async def symbols_to_complete(day: date, extra: Iterable[str] = ()) -> list[str]:
    """Everything worth a full day: watchlists, anything seen live that day, current demand."""
    # Wide enough for the futures night session that belongs to ``day``.
    start = datetime.combine(previous_weekday(day), FUTURES_NIGHT_OPEN, TAIPEI)
    end = datetime.combine(day + timedelta(days=1), time(0), TAIPEI)
    async with session_factory() as session:
        watched = (await session.scalars(select(WatchlistItemRow.symbol).distinct())).all()
        seen = (
            await session.scalars(
                select(QuoteMinuteRow.symbol)
                .where(QuoteMinuteRow.minute >= start, QuoteMinuteRow.minute < end)
                .distinct()
            )
        ).all()
    symbols = {s.strip().upper() for s in (*watched, *seen, *extra)}
    return sorted(s for s in symbols if SYMBOL.fullmatch(s))


async def backfill(
    fetch: Fetch,
    windows: Iterable[tuple[str, datetime, datetime]],
    *,
    replace: bool,
    pause: float = QUERY_INTERVAL_SECONDS,
    on_done: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, int]:
    """Fetch and store official minutes per ``(symbol, start, end)``; rows written each.

    Only minutes with ``start <= minute < end`` are written. Raises BudgetExhausted
    (after logging) when the shared Shioaji budget says stop.
    """
    written: dict[str, int] = {}
    for index, (symbol, start, end) in enumerate(windows):
        if index and pause:
            await asyncio.sleep(pause)
        try:
            rows = await fetch(symbol, start, end)
        except BudgetExhausted:
            log.info("Shioaji query budget reached; minute backfill paused")
            raise
        except (RuntimeError, ValueError, LookupError):
            # SDK messages can contain account data; keep logs generic.
            log.warning("Minute backfill skipped one symbol")
            continue
        rows = [row for row in rows if start <= row["minute"] < end]
        if replace and rows:
            compared, matched = alignment(await live_minutes(symbol, start, end), rows)
            if compared >= 10 and matched / compared < 0.8:
                log.warning(
                    "Official minutes disagree with live minutes for %s: %d/%d closes match",
                    symbol,
                    matched,
                    compared,
                )
        written[symbol] = await store_official_minutes(rows, replace=replace)
        if on_done is not None:
            await on_done(symbol)
    return written


@dataclass(frozen=True)
class _Job:
    kind: str  # "intraday" or "close"
    day: date | None = None
    symbol: str = ""
    start: datetime | None = None
    end: datetime | None = None


class MinuteBackfiller:
    """Schedules backfills inside the collector and runs them one at a time."""

    def __init__(
        self,
        redis: Any,
        settings: Any,
        fetch: Fetch,
        *,
        now: Callable[[], datetime] = taipei_now,
        pause: float = QUERY_INTERVAL_SECONDS,
    ) -> None:
        self.redis = redis
        self.fetch = fetch
        self.now = now
        self.pause = pause
        self.after_close = parse_clock(settings.quotes_backfill_after_close)
        self.max_symbols = settings.quotes_backfill_max_symbols
        self.queue: asyncio.Queue[_Job] = asyncio.Queue(maxsize=1000)

    @staticmethod
    def close_key(day: date) -> str:
        return f"{KEY}close:{day.isoformat()}"

    async def on_subscribed(self, symbol: str, at: datetime | None = None) -> bool:
        """Queue the intraday catch-up once per symbol per session. Never raises."""
        try:
            window = intraday_window(symbol, at or self.now())
            if window is None:
                return False
            start, end = window
            close_state = await self.redis.get(self.close_key(end.date()))
            if close_state == "done" and not is_futures(symbol):
                return False  # Already replaced with the official day.
            key = f"{KEY}intraday:{symbol}:{start.isoformat()}"
            if not await self.redis.set(key, "1", nx=True, ex=DEDUPE_TTL_SECONDS):
                return False
            self.queue.put_nowait(_Job("intraday", symbol=symbol, start=start, end=end))
            return True
        except Exception:
            log.warning("Intraday minute backfill not scheduled")
            return False

    async def check_after_close(self, extra: Iterable[str] = ()) -> bool:
        """Claim and queue today's after-close completion when due. Never raises."""
        now = self.now()
        if not post_close_due(now, self.after_close):
            return False
        day = now.astimezone(TAIPEI).date()
        try:
            if not await self.redis.set(
                self.close_key(day), "running", nx=True, ex=CLAIM_TTL_SECONDS
            ):
                return False
        except Exception:
            log.warning("After-close minute backfill not scheduled")
            return False
        self.queue.put_nowait(_Job("close", day=day, symbol=",".join(sorted(set(extra)))))
        return True

    async def run(self) -> None:
        """Consume the queue forever (cancel to stop); one history query at a time."""
        while True:
            job = await self.queue.get()
            try:
                if job.kind == "intraday":
                    await self._intraday(job)
                else:
                    await self._close(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Minute backfill job failed")

    async def _intraday(self, job: _Job) -> None:
        try:
            await backfill(self.fetch, [(job.symbol, job.start, job.end)], replace=False, pause=0)
        except BudgetExhausted:
            pass  # Quietly skipped; the after-close run completes the day.

    async def _close(self, job: _Job) -> None:
        key = self.close_key(job.day)
        done_key = key + ":done"
        try:
            extra = [s for s in job.symbol.split(",") if s]
            symbols = (await symbols_to_complete(job.day, extra))[: self.max_symbols]
            finished = set(await self.redis.smembers(done_key))
            windows = [
                (symbol, *official_window(symbol, job.day))
                for symbol in symbols
                if symbol not in finished
            ]

            async def mark(symbol: str) -> None:
                await self.redis.sadd(done_key, symbol)
                await self.redis.expire(done_key, DEDUPE_TTL_SECONDS)

            written = await backfill(
                self.fetch, windows, replace=True, pause=self.pause, on_done=mark
            )
            await self.redis.set(key, "done", ex=DEDUPE_TTL_SECONDS)
            log.info(
                "After-close minute backfill: %d symbols, %d minutes",
                len(written),
                sum(written.values()),
            )
        except BudgetExhausted:
            await self._release(key, retry_after=RETRY_AFTER_BUDGET_SECONDS)
        except BaseException:
            await self._release(key)
            raise

    async def _release(self, key: str, retry_after: int | None = None) -> None:
        try:
            if retry_after:
                await self.redis.set(key, "retry", ex=retry_after)
            else:
                await self.redis.delete(key)
        except Exception:
            log.warning("After-close minute backfill claim not released; it expires later")


def main() -> None:
    """Manual catch-up: ``quant-signal-backfill-minutes --date 2026-11-26 [--symbol 2330.TW]``.

    Opens its own Shioaji connection (each account allows up to 5) and draws from the
    same Redis query budget as the collector, so it can never push the account over
    Shioaji's limits. Run it outside the session when possible.
    """
    from redis.asyncio import Redis

    from quant_signal.infrastructure.providers.shioaji_budget import ShioajiBudget
    from quant_signal.infrastructure.providers.shioaji_feed import ShioajiFeed
    from quant_signal.settings import get_settings

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--date", type=date.fromisoformat, default=taipei_now().date())
    parser.add_argument("--symbol", action="append", default=[])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    async def run() -> None:
        redis = Redis.from_url(
            settings.quotes_redis_url.get_secret_value(),
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        feed = ShioajiFeed(
            settings,
            lambda tick: None,
            lambda *a: None,
            budget=ShioajiBudget.from_settings(redis, settings),
        )
        try:
            requested = [s.strip().upper() for s in args.symbol]
            if any(not SYMBOL.fullmatch(s) for s in requested):
                raise SystemExit("Symbols must look like 2330.TW, 6488.TWO or TXF.TAIFEX")
            symbols = requested or await symbols_to_complete(args.date)
            symbols = symbols[: settings.quotes_backfill_max_symbols]
            await asyncio.to_thread(feed.start)
            windows = [(s, *official_window(s, args.date)) for s in symbols]
            try:
                written = await backfill(feed.minute_bars, windows, replace=True)
            except BudgetExhausted:
                raise SystemExit("Shioaji query budget reached; try again later") from None
            log.info("Backfilled %d symbols, %d minutes", len(written), sum(written.values()))
        finally:
            await asyncio.to_thread(feed.close)
            await redis.aclose()

    asyncio.run(run())
