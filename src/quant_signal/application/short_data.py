"""Scheduled refresh of securities-lending, day-trade, insider-pledge and dividend data.

Runs inside the sync worker tick; a failure is logged, remembered in memory and retried
after ``retry_after``; nothing here raises into the price sync.

- ``sbl`` (TWSE TWT93U + TPEx margin_sbl) and ``daytrade`` (TWSE TWTB4U): the exchanges
  publish in the evening (SBL table refreshed about 20:30 and 22:30 Taipei) and TWSE
  revises day-trade figures on T+1 and T+2, so the last ``RECENT_SESSIONS`` weekdays are
  re-read once per evening slot (``m`` before 19:00, ``a`` from 19:00, ``b`` from 20:35,
  ``c`` from 22:35). A changed number becomes a new ``revision``; identical data writes
  nothing. Today's SBL is only read from slot ``b`` (after the first official update).
- Backfill: the last ``backfill_sessions`` weekdays before the recent window, at most
  ``backfill_per_tick`` days per tick, never during the trading session (weekdays
  08:30-14:30 Taipei), each day and *source* (TWSE SBL, TPEx SBL, TWSE day trade) recorded
  once in ``fetch_coverage`` (``daily_sbl_twse`` ... keys of at most 16 characters).
  Backfilled rows get the documented publication time as ``available_at`` (never later than
  the fetch).
- ``insider`` (monthly pledge tables) once a day, ``dividend`` (除權除息預告表) twice a day.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol

import httpx

from quant_signal.application.data_sync import TAIPEI
from quant_signal.application.holders_data import _rollback, tdcc_slot
from quant_signal.application.publication_time import (
    DAYTRADE_PUBLISHED,
    SBL_PUBLISHED,
    publication_moment,
    stamp_available_at,
)
from quant_signal.application.source_guard import Backoff, MissingSources, SourceBlocked, retrying
from quant_signal.domain.models import FetchCoverage
from quant_signal.infrastructure.providers import short_data as src

logger = logging.getLogger(__name__)

SOURCE = "short_data"
SBL = "sbl"
DAYTRADE = "daytrade"
INSIDER = "insider"
DIVIDEND = "dividend"
RETRY_AFTER = timedelta(minutes=10)
RECENT_SESSIONS = 3
MIN_TWSE_SBL_ROWS = 500  # ~1,000 TWSE common stocks have a row
MIN_TPEX_SBL_ROWS = 300  # ~800 OTC common stocks
MIN_DAYTRADE_ROWS = 300
MIN_PLEDGE_ROWS = 800  # companies; TWSE ~1,000 and TPEx ~800


class _HasClient(Protocol):
    client: httpx.AsyncClient


def weekdays_back(day: date, count: int) -> list[date]:
    """``count`` weekdays strictly before ``day``, newest first."""
    out: list[date] = []
    cursor = day
    while len(out) < count:
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            out.append(cursor)
    return out


def add_weekdays(day: date, count: int) -> date:
    cursor = day
    while count > 0:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            count -= 1
    return cursor


def evening_slot(now: datetime) -> str:
    """``m`` (morning/day), ``a`` 19:00+, ``b`` 20:35+, ``c`` 22:35+ in Taipei time."""
    local = now.astimezone(TAIPEI).time()
    if local >= time(22, 35):
        return "c"
    if local >= time(20, 35):
        return "b"
    if local >= time(19, 0):
        return "a"
    return "m"


def recent_days(now: datetime) -> list[date]:
    """The weekdays whose figures may still be published or revised, oldest first.

    Today only counts once the evening publication has started (slot ``a`` or later).
    """
    local = now.astimezone(TAIPEI)
    today = local.date()
    days = weekdays_back(today + timedelta(days=1), RECENT_SESSIONS + 1)  # includes today
    days = [d for d in days if d != today or (today.weekday() < 5 and evening_slot(now) != "m")]
    return sorted(days)[-RECENT_SESSIONS:] if len(days) >= RECENT_SESSIONS else sorted(days)


def backfill_days(now: datetime, sessions: int) -> list[date]:
    """Weekdays before the recent window, newest first, ``sessions`` of them."""
    recent = recent_days(now)
    anchor = min(recent) if recent else now.astimezone(TAIPEI).date()
    return weekdays_back(anchor, sessions)


def backfill_allowed(now: datetime) -> bool:
    """Backfill runs only outside the trading session (weekdays 08:30-14:30 Taipei)."""
    local = now.astimezone(TAIPEI)
    if local.weekday() >= 5:
        return True
    return not (time(8, 30) <= local.time() <= time(14, 30))


def documented_available_at(kind: str, day: date) -> datetime:
    """When a past session's table was final, for backfilled rows (Taipei -> UTC)."""
    if kind == SBL:
        return publication_moment(day, SBL_PUBLISHED)
    # day trade is final on T+2
    return publication_moment(add_weekdays(day, 2), DAYTRADE_PUBLISHED)


# Sources per dataset. Day trading is published by TWSE only; never wait for TPEx there.
EXCHANGES = {SBL: ("twse", "tpex"), DAYTRADE: ("twse",)}
Source = tuple[str, str]  # (dataset name, exchange)


def coverage_dataset(kind: str, source: Source) -> str:
    """``daily_sbl_twse`` / ``backfill_dt_tpex``: coverage is kept per source, not per day."""
    name, exchange = source
    return f"{kind}_{'sbl' if name == SBL else 'dt'}_{exchange}"


def daily_period(day: date, today: date, slot: str) -> str:
    """``20261125@1127c``: at most 16 characters (``fetch_coverage.period`` is varchar(16))."""
    return f"{day:%Y%m%d}@{today:%m%d}{slot}"


async def _record(repository: Any, dataset: str, period: str, rows: int, moment: datetime) -> None:
    await repository.record_fetch_coverage(
        [
            FetchCoverage(
                source=SOURCE,
                dataset=dataset,
                symbol="*",
                period=period,
                status="ok" if rows else "empty",
                row_count=rows,
                fetched_at=moment,
            )
        ]
    )


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, str],
    *,
    attempts: int,
    backoff: Backoff | None = None,
) -> Any:
    async def call() -> Any:
        response = await client.get(url, params=params, headers={"Accept": "*/*"})
        response.raise_for_status()
        return response.json()

    return await retrying(backoff, call, url=url, attempts=attempts)


async def _fetch_one(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, str] | None,
    parse: Callable[[Any], list],
    day: date | None,
    minimum: int,
    *,
    attempts: int,
    backoff: Backoff | None = None,
) -> list:
    payload = await _get_json(client, url, params or {}, attempts=attempts, backoff=backoff)
    rows = [row for row in parse(payload) if day is None or row.trading_date == day]
    if rows and len(rows) < minimum:
        raise RuntimeError(f"short data looks incomplete: {len(rows)} rows ({url})")
    return rows


async def _refresh_day(
    repository: Any,
    twse: _HasClient,
    tpex: _HasClient,
    day: date,
    *,
    sources: list[Source],
    moment: datetime,
    backfill: bool,
    fetch_attempts: int,
    request_interval: float,
    backoff: Backoff | None = None,
) -> tuple[dict[Source, int], dict[str, int], list[Source]]:
    """Fetch the given sources of one day: (rows parsed per source, rows written per dataset,
    sources that failed)."""
    counts: dict[Source, int] = {}
    written = {name: 0 for name, _ in sources}
    failed: list[Source] = []
    # Same rule for the live window and the back-fill: a past day fetched late is stamped
    # with its documented publication time, never with the fetch time.
    sbl_at = stamp_available_at(documented_available_at(SBL, day), moment)
    dt_at = stamp_available_at(documented_available_at(DAYTRADE, day), moment)
    plan: dict[Source, tuple[_HasClient, str, dict[str, str], Callable, int, Callable]] = {
        (SBL, "twse"): (
            twse,
            src.TWSE_SBL_URL,
            src.twse_params(day),
            lambda p: src.parse_twse_sbl(p, available_at=sbl_at),
            MIN_TWSE_SBL_ROWS,
            repository.upsert_short_lending,
        ),
        (SBL, "tpex"): (
            tpex,
            src.TPEX_SBL_URL,
            src.tpex_sbl_params(day),
            lambda p: src.parse_tpex_sbl(p, available_at=sbl_at),
            MIN_TPEX_SBL_ROWS,
            repository.upsert_short_lending,
        ),
        (DAYTRADE, "twse"): (
            twse,
            src.TWSE_DAYTRADE_URL,
            src.twse_params(day),
            lambda p: src.parse_twse_daytrade(p, available_at=dt_at),
            MIN_DAYTRADE_ROWS,
            repository.upsert_day_trade,
        ),
    }
    for index, source in enumerate(sources):
        provider, url, params, parse, minimum, upsert = plan[source]
        if index and request_interval:
            await asyncio.sleep(request_interval)
        try:
            rows = await _fetch_one(
                provider.client,
                url,
                params,
                parse,
                day,
                minimum,
                attempts=fetch_attempts,
                backoff=backoff,
            )
            counts[source] = len(rows)
            written[source[0]] += await upsert(rows) if rows else 0
        except SourceBlocked:
            failed.append(source)
            logger.warning("short data source unavailable: %s %s", url, day)
            await _rollback(repository)
        except Exception:
            failed.append(source)
            logger.exception("short data request failed: %s %s", url, day)
            await _rollback(repository)
    return counts, written, failed


async def _refresh_daily(
    repository: Any,
    twse: _HasClient,
    tpex: _HasClient,
    *,
    moment: datetime,
    backfill_sessions: int,
    backfill_per_tick: int,
    fetch_attempts: int,
    request_interval: float,
    last_failure: dict[str, datetime],
    written: dict[str, int],
    sbl_enabled: bool,
    daytrade_enabled: bool,
    retry_after: timedelta = RETRY_AFTER,
) -> None:
    local = moment.astimezone(TAIPEI)
    today = local.date()
    slot = evening_slot(moment)
    backoff = Backoff(last_failure, moment, retry_after)
    tracker = MissingSources(
        repository, source=SOURCE, moment=moment, last_failure=last_failure, retry_after=retry_after
    )

    def wanted(day: date) -> list[Source]:
        names: list[str] = []
        if sbl_enabled and (day < today or slot in ("b", "c")):
            names.append(SBL)
        if daytrade_enabled:
            names.append(DAYTRADE)
        return [(name, exchange) for name in names for exchange in EXCHANGES[name]]

    # 1) recent days, re-read once per evening slot so T+1/T+2 revisions are caught
    fetched_days = 0
    for day in recent_days(moment):
        sources = wanted(day)
        if not sources:
            continue
        period, key = daily_period(day, today, slot), f"{day:%Y%m%d}"
        past = day < today
        final = past or slot == "c"
        covered = {
            source: await repository.list_fetch_coverage(
                SOURCE, coverage_dataset("daily", source), "*", [period]
            )
            for source in sources
        }
        due: list[Source] = []
        for source in sources:
            if period in covered[source]:
                continue
            if await tracker.due(coverage_dataset("daily", source), key, final=final):
                due.append(source)
        if not due:
            continue
        if fetched_days and request_interval:
            await asyncio.sleep(request_interval)
        fetched_days += 1
        counts, new, failed = await _refresh_day(
            repository,
            twse,
            tpex,
            day,
            sources=due,
            moment=moment,
            backfill=False,
            fetch_attempts=fetch_attempts,
            request_interval=request_interval,
            backoff=backoff,
        )
        for name, count in new.items():
            written[name] = written.get(name, 0) + count
        day_has_rows = any(counts.values()) or any(
            covered[source][period].status == "ok"
            for source in sources
            if period in covered[source]
        )
        for source, rows in counts.items():
            if failed and not rows:
                continue  # an exchange that failed may be why this one looks empty
            await tracker.settle(
                coverage_dataset("daily", source),
                period,
                key,
                rows,
                day_has_rows=day_has_rows,
                past=past,
                final=final,
            )
        logger.info("short data %s (%s): rows %s, new %s", day, slot, counts, new)
        if failed:
            last_failure["daily"] = moment
            return  # retry later; do not keep hitting an exchange that is failing

    # 2) gradual, off-session backfill of older sessions
    if backfill_sessions <= 0 or not backfill_allowed(moment):
        return
    older = backfill_days(moment, backfill_sessions)
    sources = [
        (name, exchange)
        for name, on in ((SBL, sbl_enabled), (DAYTRADE, daytrade_enabled))
        if on
        for exchange in EXCHANGES[name]
    ]
    periods = [f"bf:{day.isoformat()}" for day in older]
    cover = {
        source: await repository.list_fetch_coverage(
            SOURCE, coverage_dataset("backfill", source), "*", periods
        )
        for source in sources
    }
    todo: list[tuple[date, str, list[Source]]] = []
    for day, period in zip(older, periods, strict=True):
        due = [
            source
            for source in sources
            if period not in cover[source]
            and await tracker.due(coverage_dataset("backfill", source), period, final=True)
        ]
        if due:
            todo.append((day, period, due))
        if len(todo) >= backfill_per_tick:
            break
    for index, (day, period, due) in enumerate(todo):
        if index and request_interval:
            await asyncio.sleep(request_interval)
        counts, new, failed = await _refresh_day(
            repository,
            twse,
            tpex,
            day,
            sources=due,
            moment=moment,
            backfill=True,
            fetch_attempts=fetch_attempts,
            request_interval=request_interval,
            backoff=backoff,
        )
        for name, count in new.items():
            written[name] = written.get(name, 0) + count
        day_has_rows = any(counts.values()) or any(
            cover[source][period].status == "ok" for source in sources if period in cover[source]
        )
        for source, rows in counts.items():
            if failed and not rows:
                continue
            await tracker.settle(
                coverage_dataset("backfill", source),
                period,
                period,
                rows,
                day_has_rows=day_has_rows,
                past=True,
                final=True,
            )
        logger.info("short data backfill %s: rows %s, new %s", day, counts, new)
        if failed:
            last_failure["daily"] = moment
            return


async def _refresh_insiders(
    repository: Any,
    twse: _HasClient,
    tpex: _HasClient,
    *,
    moment: datetime,
    fetch_attempts: int,
    backoff: Backoff | None = None,
) -> tuple[int, int]:
    rows: list = []
    for provider, url, parse in (
        (twse, src.TWSE_PLEDGE_URL, src.parse_twse_pledges),
        (tpex, src.TPEX_PLEDGE_URL, src.parse_tpex_pledges),
    ):
        payload = await _get_json(
            provider.client, url, {}, attempts=fetch_attempts, backoff=backoff
        )
        rows += parse(payload, available_at=moment)
    if len(rows) < MIN_PLEDGE_ROWS:
        raise RuntimeError(f"insider holdings look incomplete: {len(rows)} companies")
    return len(rows), await repository.upsert_insider_holdings(rows)


async def _refresh_dividends(
    repository: Any,
    twse: _HasClient,
    tpex: _HasClient,
    *,
    moment: datetime,
    fetch_attempts: int,
    backoff: Backoff | None = None,
) -> tuple[int, int]:
    """Each exchange's forecast table is the complete list of its upcoming events: stored
    events of that source for a future ex-date inside the table's span that are no longer
    listed (moved, re-typed or cancelled) are withdrawn, not left to be credited twice."""
    total = new = 0
    today = moment.astimezone(TAIPEI).date()
    for provider, url, parse in (
        (twse, src.TWSE_DIVIDEND_URL, src.parse_twse_dividends),
        (tpex, src.TPEX_DIVIDEND_URL, src.parse_tpex_dividends),
    ):
        payload = await _get_json(
            provider.client, url, {}, attempts=fetch_attempts, backoff=backoff
        )
        rows = parse(payload, available_at=moment)
        total += len(rows)
        new += await repository.upsert_dividend_events(rows)
        if rows:
            new += await repository.withdraw_unlisted_dividend_events(
                rows[0].source, rows, today=today, available_at=moment
            )
    return total, new


async def refresh_short_data(
    repository: Any,
    twse: _HasClient,
    tpex: _HasClient,
    *,
    now: datetime | None = None,
    attempts: dict[str, datetime] | None = None,
    sbl_enabled: bool = True,
    daytrade_enabled: bool = True,
    insider_enabled: bool = True,
    dividend_enabled: bool = True,
    backfill_sessions: int = 60,
    backfill_per_tick: int = 2,
    retry_after: timedelta = RETRY_AFTER,
    fetch_attempts: int = 3,
    request_interval: float = 0.35,
) -> dict[str, int]:
    """Run every dataset that is due; returns rows written per dataset."""
    moment = now or datetime.now(UTC)
    last_failure = attempts if attempts is not None else {}
    written: dict[str, int] = {}

    def throttled(name: str) -> bool:
        failed_at = last_failure.get(name)
        return failed_at is not None and moment - failed_at < retry_after

    if (sbl_enabled or daytrade_enabled) and not throttled("daily"):
        try:
            await _refresh_daily(
                repository,
                twse,
                tpex,
                moment=moment,
                backfill_sessions=backfill_sessions,
                backfill_per_tick=backfill_per_tick,
                fetch_attempts=fetch_attempts,
                request_interval=request_interval,
                last_failure=last_failure,
                written=written,
                sbl_enabled=sbl_enabled,
                daytrade_enabled=daytrade_enabled,
                retry_after=retry_after,
            )
            if last_failure.get("daily") != moment:
                last_failure.pop("daily", None)  # a clean pass clears the earlier failure
        except Exception:
            last_failure["daily"] = moment
            logger.exception("short data daily refresh failed")
            await _rollback(repository)

    local = moment.astimezone(TAIPEI)
    jobs: list[tuple[str, bool, str | None, Callable]] = [
        (INSIDER, insider_enabled and local.hour >= 8, local.date().isoformat(), _refresh_insiders),
        (DIVIDEND, dividend_enabled, tdcc_slot(moment), _refresh_dividends),
    ]
    for name, enabled, slot, job in jobs:
        if not enabled or not slot or throttled(name):
            continue
        done = await repository.list_fetch_coverage(SOURCE, name, "*", [slot])
        if slot in done:
            continue
        try:
            parsed, new = await job(
                repository,
                twse,
                tpex,
                moment=moment,
                fetch_attempts=fetch_attempts,
                backoff=Backoff(last_failure, moment, retry_after),
            )
        except Exception:
            last_failure[name] = moment
            logger.exception("%s refresh failed", name)
            await _rollback(repository)
        else:
            last_failure.pop(name, None)
            written[name] = new
            await _record(repository, name, slot, parsed, moment)
            logger.info("%s: %s rows, %s new (%s)", name, parsed, new, slot)
    return written
