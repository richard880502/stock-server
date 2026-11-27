"""Scheduled refresh of the TAIFEX derivatives datasets (sync worker tick).

Each dataset has its own coverage (``fetch_coverage``, one period per trading day):

- *Live day*: from ``after_close`` (default 15:30 Taipei, institutional positions appear
  ~15:00) on a session day the reports are polled; a day whose data is missing or
  incomplete is retried after ``retry_after`` until it appears, then never again.
- *Back-fill*: the last ``backfill_sessions`` sessions, newest first, one date-range
  download per request (one month at most for the large reports), a few requests per tick,
  and only outside the 08:30 - ``after_close`` trading window.
- TXO open-interest strikes need a ~0.7 MB one-day file, so they are collected for the live
  day only and are not back-filled.

Failures are logged, remembered and retried after ``retry_after``; nothing raises into the
price sync. TAIFEX redirects clients that burst, hence the small per-tick request budget.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Any, Protocol

from quant_signal.application.data_sync import TAIPEI
from quant_signal.application.derivatives_observations import refresh_observation_futures
from quant_signal.domain.models import FetchCoverage
from quant_signal.infrastructure.providers import taifex as src

logger = logging.getLogger(__name__)

SOURCE = "taifex_derivatives"
RETRY_AFTER = timedelta(minutes=15)
SPECS_TTL = timedelta(days=7)
SPECS_RETRY_TTL = timedelta(days=1)  # when the adjusted-contract list could not be read
MIN_SPECS = 250  # ~326 stock-futures underlying products
MIN_STOCK_FUTURES_DAY = 150  # underlyings with a row on a normal day (~325)
SESSION_START = time(8, 30)
VIX_MONTHS_AVAILABLE = 4  # TAIFEX keeps only the last four monthly files


class Fetcher(Protocol):
    async def fetch(self, url: str, form: dict[str, str] | None = None) -> str: ...


@lru_cache(maxsize=1)
def _calendar() -> Any | None:
    try:
        import exchange_calendars as xcals

        return xcals.get_calendar("XTAI")
    except Exception:  # pragma: no cover - the weekday fallback below still works
        logger.warning("XTAI calendar unavailable; using weekdays as sessions")
        return None


def is_session(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    calendar = _calendar()
    if calendar is None:
        return True
    try:
        import pandas as pd

        return bool(calendar.is_session(pd.Timestamp(day)))
    except Exception:  # outside the calendar's range
        return True


def recent_sessions(end: date, count: int) -> list[date]:
    """The last ``count`` trading sessions up to and including ``end``, oldest first."""
    days: list[date] = []
    day = end
    while len(days) < count and (end - day).days < count * 3 + 14:
        if is_session(day):
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


def _parse_clock(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


@dataclass(frozen=True)
class RangeDataset:
    name: str
    url: str
    form: Callable[[date, date], dict[str, str]]
    parse: Callable[[str], list[Any]]
    write: str  # repository method
    chunk_days: int  # longest date range one request may ask for
    complete: Callable[[list[Any]], bool]  # is this one day's slice fully published?
    # Runs once a request is due and before it; returns the extra requests it made.
    prepare: Callable[[], Awaitable[int]] | None = None


def _tx_foreign_trust_dealer(rows: list[Any]) -> bool:
    return {r.investor for r in rows if r.contract == "TX"} == {"foreign", "trust", "dealer"}


def _all_options_sides(rows: list[Any]) -> bool:
    return len({(r.call_put, r.investor) for r in rows}) == 6


def _has_all_traders(rows: list[Any]) -> bool:
    return any(r.trader_type == "all" for r in rows)


def build_datasets(taifex: Fetcher, cache: dict[str, Any], moment: datetime) -> list[RangeDataset]:
    async def prepare_specs() -> int:
        _, used = await _load_specs(taifex, cache, moment)
        return used

    def parse_stock_futures(text: str) -> list[Any]:
        specs: dict[str, src.ContractSpec] = cache["specs"]
        return src.build_stock_futures_days(src.parse_futures_daily(text, set(specs)), specs)

    return [
        RangeDataset(
            "futures_institutional",
            src.FUT_INSTITUTIONAL_URL,
            src.fut_institutional_form,
            src.parse_futures_institutional,
            "upsert_futures_institutional",
            120,
            _tx_foreign_trust_dealer,
        ),
        RangeDataset(
            "options_pc_ratio",
            src.PC_RATIO_URL,
            src.range_form,
            src.parse_pc_ratio,
            "upsert_options_pc_ratio",
            120,
            bool,
        ),
        RangeDataset(
            "options_institutional",
            src.CALLS_PUTS_URL,
            src.calls_puts_form,
            src.parse_options_institutional,
            "upsert_options_institutional",
            120,
            _all_options_sides,
        ),
        RangeDataset(
            "futures_large_traders",
            src.LARGE_TRADERS_URL,
            src.range_form,
            src.parse_large_traders,
            "upsert_futures_large_traders",
            28,
            _has_all_traders,
        ),
        RangeDataset(
            "stock_futures",
            src.FUT_DAILY_URL,
            src.fut_daily_form,
            parse_stock_futures,
            "upsert_stock_futures",
            28,
            lambda rows: len(rows) >= MIN_STOCK_FUTURES_DAY,
            prepare_specs,
        ),
    ]


async def _rollback(repository: Any) -> None:
    rollback = getattr(getattr(repository, "session", None), "rollback", None)
    if rollback is not None:  # a failed statement poisons the shared session
        await rollback()


async def _record(
    repository: Any, dataset: str, results: dict[date, int], moment: datetime
) -> None:
    await repository.record_fetch_coverage(
        [
            FetchCoverage(
                source=SOURCE,
                dataset=dataset,
                symbol="*",
                period=day.isoformat(),
                status="ok" if rows else "empty",
                row_count=rows,
                fetched_at=moment,
            )
            for day, rows in results.items()
        ]
    )


def _chunk(missing: list[date], span_days: int) -> list[date]:
    """The newest missing day plus every older one within ``span_days`` of it."""
    newest = max(missing)
    return sorted(day for day in missing if (newest - day).days <= span_days)


async def _load_specs(
    taifex: Fetcher, cache: dict[str, Any], moment: datetime
) -> tuple[dict[str, src.ContractSpec] | None, int]:
    """Stock-futures products (official underlying list + adjusted contracts), cached a week.

    Returns the mapping (None when it cannot be loaded now) and the requests made.
    """
    expires = cache.get("expires")
    if cache.get("specs") and expires and moment < expires:
        return cache["specs"], 0
    requests = 1
    base = src.parse_stock_lists(await taifex.fetch(src.STOCK_LISTS_URL))
    if len(base) < MIN_SPECS:
        raise RuntimeError(f"stock-futures underlying list looks incomplete: {len(base)} rows")
    specs = {spec.code: spec for spec in base}
    ttl = SPECS_TTL
    requests += 1
    try:
        for spec in src.parse_ssf_adjusted(await taifex.fetch(src.SSF_ADJUSTED_URL)):
            specs.setdefault(spec.code, spec)
    except Exception:
        ttl = SPECS_RETRY_TTL
        logger.exception("stock-futures adjusted contract list unavailable; using base list")
    cache["specs"], cache["expires"] = specs, moment + ttl
    return specs, requests


async def _refresh_range_dataset(
    repository: Any,
    taifex: Fetcher,
    dataset: RangeDataset,
    sessions: list[date],
    live_days: set[date],
    moment: datetime,
) -> tuple[int, int, bool]:
    """Fetch the newest missing chunk. Returns (rows written, requests made, live day pending)."""
    done = await repository.list_fetch_coverage(
        SOURCE, dataset.name, "*", [day.isoformat() for day in sessions]
    )
    missing = [day for day in sessions if day.isoformat() not in done]
    if not missing:
        return 0, 0, False
    chunk = _chunk(missing, dataset.chunk_days)
    used = await dataset.prepare() if dataset.prepare else 0
    text = await taifex.fetch(dataset.url, dataset.form(min(chunk), max(chunk)))
    parsed = dataset.parse(text)
    by_day: dict[date, list[Any]] = defaultdict(list)
    for row in parsed:
        by_day[row.trading_date].append(row)
    pending = False
    results: dict[date, int] = {}
    to_write: list[Any] = []
    for day in chunk:
        rows = by_day.get(day, [])
        if rows and dataset.complete(rows):
            to_write.extend(rows)
            results[day] = len(rows)
        elif day in live_days:
            pending = True  # not (fully) published yet: ask again later
        else:
            results[day] = 0  # a finished day TAIFEX has nothing (usable) for
    written = await getattr(repository, dataset.write)(to_write) if to_write else 0
    await _record(repository, dataset.name, results, moment)
    logger.info(
        "derivatives %s %s..%s: %s rows, %s new%s",
        dataset.name,
        min(chunk),
        max(chunk),
        len(to_write),
        written,
        ", live day pending" if pending else "",
    )
    return written, used + 1, pending


async def _refresh_vix(
    repository: Any, taifex: Fetcher, sessions: list[date], live_days: set[date], moment: datetime
) -> tuple[int, int, bool]:
    done = await repository.list_fetch_coverage(
        SOURCE, "vix", "*", [day.isoformat() for day in sessions]
    )
    missing = [day for day in sessions if day.isoformat() not in done]
    newest = max(sessions)
    missing = [
        day
        for day in missing
        if (newest.year - day.year) * 12 + newest.month - day.month < VIX_MONTHS_AVAILABLE
    ]
    if not missing:
        return 0, 0, False
    month = max(missing).replace(day=1)
    wanted = [day for day in missing if day.replace(day=1) == month]
    rows = src.parse_vix(await taifex.fetch(src.vix_url(month)))
    by_day = {row.trading_date: row for row in rows}
    results = {day: int(day in by_day) for day in wanted if day in by_day or day not in live_days}
    written = await repository.upsert_vix_daily([by_day[day] for day in wanted if day in by_day])
    await _record(repository, "vix", results, moment)
    pending = any(day in live_days and day not in by_day for day in wanted)
    logger.info("derivatives vix %s: %s rows, %s new", month, len(by_day), written)
    return written, 1, pending


async def _refresh_oi_levels(
    repository: Any, taifex: Fetcher, day: date, moment: datetime
) -> tuple[int, int, bool]:
    done = await repository.list_fetch_coverage(SOURCE, "options_oi_levels", "*", [day.isoformat()])
    if done:
        return 0, 0, False
    rows = src.parse_options_daily(await taifex.fetch(src.OPT_DAILY_URL, src.opt_daily_form(day)))
    levels = src.options_oi_levels([row for row in rows if row.trading_date == day])
    if not levels:
        return 0, 1, True  # not published yet
    written = await repository.upsert_options_oi_levels(levels)
    await _record(repository, "options_oi_levels", {day: len(levels)}, moment)
    logger.info("derivatives options_oi_levels %s: %s new", day, written)
    return written, 1, False


async def refresh_derivatives_data(
    repository: Any,
    taifex: Fetcher,
    *,
    now: datetime | None = None,
    attempts: dict[str, datetime] | None = None,
    specs_cache: dict[str, Any] | None = None,
    backfill_sessions: int = 120,
    after_close: str = "15:30",
    request_budget: int = 4,
    retry_after: timedelta = RETRY_AFTER,
) -> dict[str, int]:
    """Run what is due; returns rows written per dataset. Never raises per-dataset errors."""
    moment = now or datetime.now(UTC)
    local = moment.astimezone(TAIPEI)
    today = local.date()
    cutoff = _parse_clock(after_close)
    failures = attempts if attempts is not None else {}
    cache = specs_cache if specs_cache is not None else {}
    written: dict[str, int] = {}

    live_ready = is_session(today) and local.time() >= cutoff
    in_trading_window = is_session(today) and SESSION_START <= local.time() < cutoff
    if in_trading_window:
        return written  # nothing is published yet and a back-fill is not worth the load
    end = today if live_ready else today - timedelta(days=1)
    sessions = recent_sessions(end, max(backfill_sessions, 1))
    if not sessions:
        return written
    live_days = {today} if live_ready else set()
    if backfill_sessions <= 0:
        sessions = [day for day in sessions if day in live_days]
        if not sessions:
            return written

    def throttled(name: str) -> bool:
        failed_at = failures.get(name)
        return failed_at is not None and moment - failed_at < retry_after

    budget = request_budget
    datasets: list[tuple[str, Callable[[], Awaitable[tuple[int, int, bool]]]]] = [
        (
            dataset.name,
            lambda ds=dataset: _refresh_range_dataset(
                repository, taifex, ds, sessions, live_days, moment
            ),
        )
        for dataset in build_datasets(taifex, cache, moment)
    ]
    vix = ("vix", lambda: _refresh_vix(repository, taifex, sessions, live_days, moment))
    datasets.insert(4, vix)
    if live_ready and today in sessions:
        datasets.append(
            ("options_oi_levels", lambda: _refresh_oi_levels(repository, taifex, today, moment))
        )

    for name, run in datasets:
        if budget <= 0:
            break
        if throttled(name):
            continue
        try:
            new, used, pending = await run()
        except Exception:
            failures[name] = moment
            logger.exception("derivatives refresh failed: %s", name)
            await _rollback(repository)
            budget -= 1
            continue
        budget -= used
        if used:
            written[name] = new
        if pending:
            failures[name] = moment  # published later: look again after the retry delay
        else:
            failures.pop(name, None)

    if written.get("futures_institutional"):
        try:
            revised = await refresh_observation_futures(repository)
            if revised:
                logger.info("market observations revised with futures positions: %s", revised)
        except Exception:
            logger.exception("market observation futures refresh failed")
            await _rollback(repository)
    return written
