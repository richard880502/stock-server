"""Fill gaps in ``minute_bars`` from Shioaji's official 1-minute bars (kbars).

Two moments use it, both inside the collector (the only process that talks to
Shioaji):

* Intraday, the first time a symbol is subscribed in a session: the minutes before
  the live feed started are filled once (only whole minutes strictly before the
  subscription minute, so the live feed and the backfill never write the same
  minute, and existing rows are never touched).
* After the close, once per weekday (and resumed the next off-session window if it was
  interrupted): the WHOLE market (every stock in the latest
  official daily snapshot, watched and live-seen symbols first, plus TAIEX futures)
  is replaced with the official bars, so a day is complete even if nobody looked at
  it live or the collector was disconnected.

Every kbars call goes through the shared Redis budget (``shioaji_budget``) and all
history queries run one at a time from a single queue. Before and during the
whole-market run the account's daily data usage (``api.usage()``) is checked; the run
pauses when the remaining traffic drops below the configured reserve.

The whole-market run only spends queries OFF-session (``session_day(now) is None``:
14:30-15:00 and 05:00-08:00 on weekdays, weekends after Saturday 05:00). Anything sent
inside the session would count toward Shioaji's 270-per-day kbars cap that intraday
catch-ups need. When the session starts mid-run it stops, keeps its progress (the
``:done`` set) and the unfinished day is resumed in the next off-session window,
which is why the previous weekday is checked as well.
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

from sqlalchemy import func, select

from quant_signal.application.quote_cache import FUTURES_SYMBOLS, PREFIX, SYMBOL, is_futures
from quant_signal.infrastructure.db.database import session_factory
from quant_signal.infrastructure.db.models import (
    MarketStockDayRow,
    MarketSymbolRow,
    MinuteBarRow,
    QuoteLatestRow,
    WatchlistItemRow,
)
from quant_signal.infrastructure.db.prices import MinuteBar, read_minutes, store_minutes
from quant_signal.infrastructure.providers.shioaji_budget import BudgetExhausted, session_day

log = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
STOCK_OPEN = time(9, 0)
FUTURES_DAY_OPEN = time(8, 45)
FUTURES_NIGHT_OPEN = time(15, 0)
# The budget enforces Shioaji's rate; this only spreads calls out a little.
QUERY_INTERVAL_SECONDS = 0.3
KEY = PREFIX + "backfill:"
DEDUPE_TTL_SECONDS = 2 * 86400
# Finished close keys and their done sets: long enough that Monday never redoes Friday.
DONE_TTL_SECONDS = 5 * 86400
CLAIM_TTL_SECONDS = 3600  # A crashed after-close run is retried after this at most.
RETRY_AFTER_BUDGET_SECONDS = 900
# Re-check the account's remaining traffic every this many symbols.
USAGE_CHECK_EVERY = 100
# Liquid instruments queried first to tell a market holiday from a trading day.
PROBE_SYMBOLS = ("TXF.TAIFEX", "2330.TW", "0050.TW")
CLOSED_TTL_SECONDS = 6 * 3600  # A first all-empty probe; a second one later confirms.
# The whole-market run stops this long before the session starts: the budget may wait
# up to 20 s and the SDK call takes time.
SESSION_MARGIN = timedelta(minutes=2)
MAX_CONSECUTIVE_FAILURES = 10
MAX_FAILED_SHARE = 0.2
PROGRESS_EVERY = 200

Fetch = Callable[[str, datetime, datetime], Awaitable[list[MinuteBar]]]
UsageCheck = Callable[[], Awaitable[bool]]


class UsageLow(Exception):
    """The account's remaining daily traffic is below the reserve; stop history queries."""


class SessionStarted(Exception):
    """A session-limited period began; the whole-market run must stop spending queries."""


class TooManyFailures(Exception):
    """Too many fetches raised in a row; the day must not be judged or marked done."""


def taipei_now() -> datetime:
    return datetime.now(TAIPEI)


def parse_clock(value: str) -> time:
    hour, minute = value.strip().split(":")
    return time(int(hour), int(minute))


def off_session(now: datetime) -> bool:
    """True when kbars queries do not count toward Shioaji's session-limited cap."""
    return session_day(now) is None


def clear_of_session(now: datetime, check: Callable[[datetime], bool] = off_session) -> bool:
    """Off-session now and still off-session ``SESSION_MARGIN`` from now."""
    return check(now) and check(now + SESSION_MARGIN)


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


def alignment(existing: Iterable[MinuteBar], official: Iterable[MinuteBar]) -> tuple[int, int]:
    """How many overlapping minutes have the same close (live vs official).

    A low match rate means the kbars label/timezone reading is off; the caller logs it.
    """
    official_close = {bar.minute: bar.close for bar in official}
    compared = matched = 0
    for bar in existing:
        close = official_close.get(bar.minute)
        if close is None:
            continue
        compared += 1
        if abs(close - bar.close) < 1e-6:
            matched += 1
    return compared, matched


async def store_official_minutes(rows: list[MinuteBar], *, replace: bool) -> int:
    if not rows:
        return 0
    async with session_factory() as session:
        written = await store_minutes(session, rows, live=False, replace=replace)
        await session.commit()
    return written


async def live_minutes(symbol: str, start: datetime, end: datetime) -> list[MinuteBar]:
    async with session_factory() as session:
        bars = await read_minutes(session, symbol, start, end)
    return [bar for bar in bars if not bar.official]


async def market_universe() -> list[str]:
    """Every stock in the latest official whole-market day (TWSE + TPEx), sorted."""
    async with session_factory() as session:
        latest = await session.scalar(select(func.max(MarketStockDayRow.trading_date)))
        if latest is None:
            return []
        symbols = (
            await session.scalars(
                select(MarketStockDayRow.symbol)
                .where(MarketStockDayRow.trading_date == latest)
                .distinct()
            )
        ).all()
    return sorted(s for s in {s.strip().upper() for s in symbols} if SYMBOL.fullmatch(s))


async def symbols_to_complete(
    day: date, extra: Iterable[str] = (), *, whole_market: bool = True
) -> list[str]:
    """Symbols for a full official day, most wanted first.

    Watchlists, anything seen live that day and current demand come first (so a
    paused run has done the ones people look at), then TAIEX futures, then the rest
    of the market.
    """
    # Wide enough for the futures night session that belongs to ``day``.
    start = datetime.combine(previous_weekday(day), FUTURES_NIGHT_OPEN, TAIPEI)
    end = datetime.combine(day + timedelta(days=1), time(0), TAIPEI)
    async with session_factory() as session:
        watched = (await session.scalars(select(WatchlistItemRow.symbol).distinct())).all()
        seen = (
            await session.scalars(
                select(MarketSymbolRow.symbol)
                .join(MinuteBarRow, MinuteBarRow.symbol_id == MarketSymbolRow.id)
                .where(MinuteBarRow.minute >= start, MinuteBarRow.minute < end)
                .distinct()
            )
        ).all()
    first = sorted({s.strip().upper() for s in (*watched, *seen, *extra)})
    rest = await market_universe() if whole_market else []
    ordered: list[str] = []
    for symbol in (*first, *sorted(FUTURES_SYMBOLS), *rest):
        if SYMBOL.fullmatch(symbol) and symbol not in ordered:
            ordered.append(symbol)
    return ordered


async def day_has_live_activity(day: date) -> bool:
    """True when the live feed or a snapshot saw trading on ``day`` (so it was no holiday)."""
    start = datetime.combine(day, time(0), TAIPEI)
    end = start + timedelta(days=1)
    async with session_factory() as session:
        live = await session.scalar(
            select(MinuteBarRow.minute)
            .where(
                MinuteBarRow.minute >= start,
                MinuteBarRow.minute < end,
                MinuteBarRow.official.is_(False),
            )
            .limit(1)
        )
        if live is not None:
            return True
        traded = await session.scalar(
            select(QuoteLatestRow.symbol)
            .where(QuoteLatestRow.trade_time >= start, QuoteLatestRow.trade_time < end)
            .limit(1)
        )
    return traded is not None


async def backfill(
    fetch: Fetch,
    windows: Iterable[tuple[str, datetime, datetime]],
    *,
    replace: bool,
    pause: float = QUERY_INTERVAL_SECONDS,
    on_done: Callable[[str], Awaitable[None]] | None = None,
    usage_ok: UsageCheck | None = None,
    may_query: Callable[[], bool] | None = None,
    stats: dict[str, int] | None = None,
    max_consecutive_failures: int = 0,
) -> dict[str, int]:
    """Fetch and store official minutes per ``(symbol, start, end)``; rows written each.

    Only minutes with ``start <= minute < end`` are written. Raises BudgetExhausted
    when the shared Shioaji budget says stop, and UsageLow when ``usage_ok`` reports
    the account's remaining daily traffic is below the reserve. ``may_query`` is
    checked before every symbol; when it says no, SessionStarted is raised and nothing
    is sent. ``stats`` collects ``attempted`` and ``failed`` fetch counts; with
    ``max_consecutive_failures`` > 0, TooManyFailures is raised after that many raised
    fetches in a row.
    """
    written: dict[str, int] = {}
    consecutive = 0
    for index, (symbol, start, end) in enumerate(windows):
        if may_query is not None and not may_query():
            raise SessionStarted()
        if usage_ok is not None and index % USAGE_CHECK_EVERY == 0 and not await usage_ok():
            log.info("Shioaji traffic reserve reached; minute backfill paused")
            raise UsageLow()
        if index and pause:
            await asyncio.sleep(pause)
        if stats is not None:
            stats["attempted"] = stats.get("attempted", 0) + 1
        try:
            rows = await fetch(symbol, start, end)
        except BudgetExhausted:
            log.info("Shioaji query budget reached; minute backfill paused")
            raise
        except (RuntimeError, ValueError, LookupError):
            # SDK messages can contain account data; keep logs generic.
            log.warning("Minute backfill skipped one symbol")
            if stats is not None:
                stats["failed"] = stats.get("failed", 0) + 1
            consecutive += 1
            if max_consecutive_failures and consecutive >= max_consecutive_failures:
                log.warning("Minute backfill paused after repeated failures")
                raise TooManyFailures() from None
            continue
        consecutive = 0
        rows = [row for row in rows if start <= row.minute < end]
        if replace and rows:
            live = await live_minutes(symbol, start, end)
            compared, matched = alignment(live, rows)
            if compared >= 10 and matched / compared < 0.8:
                log.warning(
                    "Official minutes disagree with live minutes for %s: %d/%d closes match "
                    "(%d live minutes, %d official, difference %+d)",
                    symbol,
                    matched,
                    compared,
                    len(live),
                    len(rows),
                    len(rows) - len(live),
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
        usage_ok: UsageCheck | None = None,
        off_session: Callable[[datetime], bool] = off_session,
        usage_probe: Callable[[], Awaitable[bool | None]] | None = None,
    ) -> None:
        self.redis = redis
        self.off_session = off_session
        self.usage_probe = usage_probe
        self.fetch = fetch
        self.usage_ok = usage_ok
        self.whole_market = getattr(settings, "quotes_backfill_whole_market", True)
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

    def _clear(self, now: datetime) -> bool:
        return clear_of_session(now, self.off_session)

    async def check_after_close(self, extra: Iterable[str] = ()) -> bool:
        """Claim and queue unfinished after-close days when off-session. Never raises.

        Today's job when due, plus the previous weekday's if it never finished (cut off
        by the night session, or a restart). Nothing older is ever retried. Nothing is
        claimed within ``SESSION_MARGIN`` of the session start.
        """
        now = self.now()
        if not self._clear(now):
            return False
        today = now.astimezone(TAIPEI).date()
        days = [previous_weekday(today)]
        if post_close_due(now, self.after_close):
            days.append(today)
        queued = False
        for day in days:
            key = self.close_key(day)
            try:
                if not await self.redis.set(key, "running", nx=True, ex=CLAIM_TTL_SECONDS):
                    continue
            except Exception:
                log.warning("After-close minute backfill not scheduled")
                return queued
            try:
                self.queue.put_nowait(_Job("close", day=day, symbol=",".join(sorted(set(extra)))))
            except Exception:
                log.warning("After-close minute backfill queue is full")
                await self._release(key)
                return queued
            queued = True
        return queued

    async def run(self) -> None:
        """Consume the queue forever (cancel to stop); one history query at a time."""
        try:
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
        except asyncio.CancelledError:
            # Queued close jobs hold a claim for an hour; give them back.
            while not self.queue.empty():
                job = self.queue.get_nowait()
                if job.kind == "close":
                    await self._release(self.close_key(job.day))
            raise

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
            symbols = (await symbols_to_complete(job.day, extra, whole_market=self.whole_market))[
                : self.max_symbols
            ]
            finished = set(await self.redis.smembers(done_key))
            handled = 0

            async def mark(symbol: str) -> None:
                nonlocal handled
                await self.redis.sadd(done_key, symbol)
                await self.redis.expire(done_key, DONE_TTL_SECONDS)
                finished.add(symbol)
                handled += 1
                if handled % PROGRESS_EVERY == 0:
                    log.info(
                        "After-close minute backfill %s: %d/%d symbols",
                        job.day.isoformat(),
                        len(finished),
                        len(symbols),
                    )

            def may_query() -> bool:
                return self._clear(self.now())

            if not finished and not await self._is_trading_day(job.day, key, mark, may_query):
                return
            windows = [
                (symbol, *official_window(symbol, job.day))
                for symbol in symbols
                if symbol not in finished
            ]
            stats: dict[str, int] = {}
            written = await backfill(
                self.fetch,
                windows,
                replace=True,
                pause=self.pause,
                on_done=mark,
                usage_ok=self.usage_ok,
                may_query=may_query,
                stats=stats,
                max_consecutive_failures=MAX_CONSECUTIVE_FAILURES,
            )
            attempted, failed = stats.get("attempted", 0), stats.get("failed", 0)
            if attempted and failed / attempted > MAX_FAILED_SHARE:
                log.warning("Minute backfill had many failed symbols; the day is not done")
                await self._release(key, retry_after=RETRY_AFTER_BUDGET_SECONDS)
                return
            await self.redis.set(key, "done", ex=DONE_TTL_SECONDS)
            log.info(
                "After-close minute backfill: %d symbols, %d minutes",
                len(written),
                sum(written.values()),
            )
        except SessionStarted:
            log.info("Session about to start; after-close backfill waits for the next window")
            await self._release(key, retry_after=RETRY_AFTER_BUDGET_SECONDS)
        except (BudgetExhausted, UsageLow, TooManyFailures):
            await self._release(key, retry_after=RETRY_AFTER_BUDGET_SECONDS)
        except BaseException:
            await self._release(key)
            raise

    async def _is_trading_day(self, day: date, key: str, mark, may_query) -> bool:
        """Probe a day nothing is known about; False means the job ends here.

        The day is only judged closed from empty answers of a few liquid instruments,
        never when the live feed saw it, never on a failed fetch or unknown usage (the
        API answers an exhausted or suspended account with EMPTY bars), and only after a
        second empty probe in a later off-session window.
        """
        if await day_has_live_activity(day):
            return True
        usage_fine = await self.usage_probe() if self.usage_probe else True
        if usage_fine is not True:
            log.info("Shioaji usage unknown or low; the day is not judged yet")
            await self._release(key, retry_after=RETRY_AFTER_BUDGET_SECONDS)
            return False
        windows = [(symbol, *official_window(symbol, day)) for symbol in PROBE_SYMBOLS]
        stats: dict[str, int] = {}
        written = await backfill(
            self.fetch, windows, replace=True, pause=self.pause, may_query=may_query, stats=stats
        )
        nonempty = [symbol for symbol, count in written.items() if count > 0]
        if nonempty:
            for symbol in nonempty:
                await mark(symbol)
            return True
        if stats.get("failed"):
            await self._release(key, retry_after=RETRY_AFTER_BUDGET_SECONDS)
            return False
        probe_key = key + ":probe"
        if await self.redis.get(probe_key):
            await self.redis.set(key, "done", ex=DONE_TTL_SECONDS)
            log.info("No market data on %s twice; treated as a closed day", day.isoformat())
        else:
            await self.redis.set(probe_key, "empty", ex=DONE_TTL_SECONDS)
            await self.redis.set(key, "closed", ex=CLOSED_TTL_SECONDS)
            log.info("No market data on %s; checked again later", day.isoformat())
        return False

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
    Shioaji's limits. A whole-market run is refused in-session; single --symbol runs are
    allowed and count against the session cap.
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
            if not requested and not clear_of_session(taipei_now()):
                raise SystemExit(
                    "A whole-market backfill would use the session-limited kbars allowance; "
                    "run it off-session (14:30-15:00 or 05:00-08:00 on weekdays, or on a "
                    "weekend), or pass --symbol for single symbols"
                )
            symbols = requested or await symbols_to_complete(args.date)
            symbols = symbols[: settings.quotes_backfill_max_symbols]
            await asyncio.to_thread(feed.start)
            windows = [(s, *official_window(s, args.date)) for s in symbols]
            reserve = settings.quotes_usage_reserve_mb * 1024 * 1024

            async def usage_ok() -> bool:
                usage = await asyncio.to_thread(feed.usage)
                return usage is None or usage["remaining_bytes"] >= reserve

            try:
                written = await backfill(
                    feed.minute_bars,
                    windows,
                    replace=True,
                    usage_ok=usage_ok,
                    may_query=None if requested else lambda: clear_of_session(taipei_now()),
                )
            except SessionStarted:
                raise SystemExit(
                    "The session is about to start; the whole-market backfill stopped. "
                    "Run it again off-session"
                ) from None
            except BudgetExhausted:
                raise SystemExit("Shioaji query budget reached; try again later") from None
            except UsageLow:
                raise SystemExit("Shioaji daily traffic reserve reached; stopped") from None
            log.info("Backfilled %d symbols, %d minutes", len(written), sum(written.values()))
        finally:
            await asyncio.to_thread(feed.close)
            await redis.aclose()

    asyncio.run(run())
