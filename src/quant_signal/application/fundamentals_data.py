"""Scheduled refresh of valuation, monthly-revenue history and quarterly financials.

Runs inside the sync worker's tick (see ``workers/sync.py``) and never raises into the
price sync: a failure is logged, remembered in memory and retried after ``retry_after``.

- ``valuation_daily``: TWSE BWIBBU_d + TPEx P/E table for each of the last
  ``valuation_window`` weekdays (today only from 17:00); a day is fetched once and
  recorded in ``fetch_coverage`` (``empty`` for holidays). Rows are stamped with the time
  we fetched them. The history of the last ``valuation_backfill_sessions`` *trading* days
  (known from ``market_stock_daily``) is filled newest first, at most
  ``backfill_per_tick`` days per tick, only outside the trading session and only when the
  live window has nothing pending; backfilled rows are stamped 18:00 Taipei of their date.
- ``revenue_history``: MOPS whole-market monthly revenue pages for the last
  ``revenue_backfill_months`` closed months, newest first, ``revenue_per_tick`` months
  (two requests each) per tick, off-session. Stamped with the statutory deadline.
- ``financials_forward``: the OpenAPI statements of the latest published quarter (all
  statement kinds, both markets), once per slot (daily in the filing months, weekly
  otherwise); stamped with the fetch time.
- ``financials_history``: MOPS statements of the last ``financials_backfill_quarters``
  quarters, newest first, one quarter (four requests) per tick, off-session, stamped with
  the statutory deadline.

Backfills only add company-periods we do not hold at all; they never revise published rows.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol

import httpx

from quant_signal.application.data_sync import TAIPEI
from quant_signal.application.holders_data import foreign_days
from quant_signal.domain.models import FetchCoverage
from quant_signal.infrastructure.providers import fundamentals as fnd
from quant_signal.infrastructure.providers import valuation as val
from quant_signal.infrastructure.providers.industry import fetch_json_list

logger = logging.getLogger(__name__)

SOURCE = "fundamentals_data"
VALUATION_DAILY = "valuation_daily"
VALUATION_BACKFILL = "valuation_backfill"
REVENUE_HISTORY = "revenue_history"
FINANCIALS_FORWARD = "financials_forward"
FINANCIALS_HISTORY = "financials_history"
RETRY_AFTER = timedelta(minutes=10)
MIN_VALUATION_ROWS = 300  # TWSE ~1,090 and TPEx ~900 rows on a normal day
MIN_REVENUE_ROWS = 300  # sii ~1,000 / otc ~880 companies per month
# Filing season (Q1 5/15, Q2 8/14, Q3 11/14, annual 3/31 plus the month before): daily polls.
FILING_MONTHS = frozenset({3, 4, 5, 7, 8, 10, 11})


class _HasClient(Protocol):
    client: httpx.AsyncClient


# --- calendar helpers -----------------------------------------------------------------


def trading_session_open(now: datetime) -> bool:
    """Weekdays 08:00-15:00 Taipei: heavy backfills stay out of the way."""
    local = now.astimezone(TAIPEI)
    return local.weekday() < 5 and 8 <= local.hour < 15


def stamped_close(day: date) -> datetime:
    """18:00 Taipei of the trading day, when the end-of-day reports can be assumed out."""
    return datetime.combine(day, time(18, 0), TAIPEI)


def financials_slot(now: datetime) -> str:
    local = now.astimezone(TAIPEI)
    if local.month in FILING_MONTHS:
        return local.date().isoformat()
    year, week, _ = local.isocalendar()
    return f"{year}-W{week:02d}"


def closed_revenue_months(now: datetime, count: int) -> list[str]:
    """The newest ``count`` months whose statutory deadline has passed, newest first."""
    local = now.astimezone(TAIPEI).date()
    index = local.year * 12 + local.month - 1
    months: list[str] = []
    while len(months) < count and index > 0:
        period = f"{index // 12:04d}-{index % 12 + 1:02d}"
        if fnd.revenue_deadline(period) <= now:
            months.append(period)
        index -= 1
    return months


def closed_quarters(now: datetime, count: int) -> list[tuple[int, int]]:
    """The newest ``count`` fiscal quarters whose statutory deadline has passed, newest first."""
    year, quarter = now.astimezone(TAIPEI).year, 4
    quarters: list[tuple[int, int]] = []
    while len(quarters) < count:
        if fnd.financials_deadline(year, quarter) <= now:
            quarters.append((year, quarter))
        quarter -= 1
        if quarter == 0:
            year, quarter = year - 1, 4
    return quarters


# --- http ---------------------------------------------------------------------------------


async def _retry(call: Any, *, attempts: int, what: str) -> Any:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await call()
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            await asyncio.sleep(min(1.5 * (2**attempt), 15))
    raise RuntimeError(f"request failed after retries: {what}") from last_error


async def _get_json(
    client: httpx.AsyncClient, url: str, params: dict[str, str], *, attempts: int
) -> Any:
    async def call() -> Any:
        response = await client.get(url, params=params, headers={"Accept": "*/*"})
        response.raise_for_status()
        return response.json()

    return await _retry(call, attempts=attempts, what=url)


async def _get_bytes(client: httpx.AsyncClient, url: str, *, attempts: int) -> bytes:
    async def call() -> bytes:
        response = await client.get(url, headers={"Accept": "*/*"})
        response.raise_for_status()
        return response.content

    return await _retry(call, attempts=attempts, what=url)


async def _post_text(
    client: httpx.AsyncClient, url: str, form: dict[str, str], *, attempts: int
) -> str:
    async def call() -> str:
        response = await client.post(url, data=form, headers={"Accept": "*/*"})
        response.raise_for_status()
        return response.content.decode("utf-8", errors="replace")

    return await _retry(call, attempts=attempts, what=url)


async def _rollback(repository: Any) -> None:
    rollback = getattr(getattr(repository, "session", None), "rollback", None)
    if rollback is not None:  # a failed statement poisons the shared session
        await rollback()


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


# --- valuation --------------------------------------------------------------------------


async def _fetch_valuation_day(
    repository: Any,
    twse: _HasClient,
    tpex: _HasClient,
    day: date,
    *,
    available_at: datetime,
    fetch_attempts: int,
    request_interval: float,
) -> tuple[int, int, int]:
    """Returns (rows parsed, rows written, failed exchange count) for one day."""
    parsed = written = failures = 0
    plan = (
        (twse, val.TWSE_VALUATION_URL, val.twse_valuation_params(day), val.parse_twse_valuation),
        (tpex, val.TPEX_VALUATION_URL, val.tpex_valuation_params(day), val.parse_tpex_valuation),
    )
    for index, (provider, url, params, parse) in enumerate(plan):
        if index and request_interval:
            await asyncio.sleep(request_interval)
        try:
            payload = await _get_json(provider.client, url, params, attempts=fetch_attempts)
            rows = [r for r in parse(payload, available_at=available_at) if r.trading_date == day]
            if rows and len(rows) < MIN_VALUATION_ROWS:
                raise RuntimeError(f"valuation looks incomplete: {len(rows)} rows ({url})")
            parsed += len(rows)
            written += await repository.upsert_valuation_daily(rows) if rows else 0
        except Exception:
            failures += 1
            logger.exception("valuation request failed: %s %s", url, day)
            await _rollback(repository)
    return parsed, written, failures


async def _refresh_valuation(
    repository: Any,
    twse: _HasClient,
    tpex: _HasClient,
    *,
    moment: datetime,
    window: int,
    backfill_sessions: int,
    backfill_per_tick: int,
    last_failure: dict[str, datetime],
    throttled: Any,
    fetch_attempts: int,
    request_interval: float,
) -> int | None:
    today = moment.astimezone(TAIPEI).date()
    total = 0
    ran = False
    if not throttled(VALUATION_DAILY):
        days = foreign_days(moment, window)
        done = await repository.list_fetch_coverage(
            SOURCE, VALUATION_DAILY, "*", [d.isoformat() for d in days]
        )
        pending = [d for d in days if d.isoformat() not in done]
        for index, day in enumerate(pending):
            ran = True
            if index and request_interval:
                await asyncio.sleep(request_interval)
            parsed, new, failures = await _fetch_valuation_day(
                repository,
                twse,
                tpex,
                day,
                available_at=moment,
                fetch_attempts=fetch_attempts,
                request_interval=request_interval,
            )
            total += new
            if failures:
                last_failure[VALUATION_DAILY] = moment
                return total  # retry later; do not keep hitting a failing exchange
            if parsed == 0 and day >= today:
                last_failure[VALUATION_DAILY] = moment  # not published yet
                return total
            await _record(repository, VALUATION_DAILY, day.isoformat(), parsed, moment)
            logger.info("valuation %s: %s rows, %s new", day, parsed, new)
        if pending:
            return total
    if backfill_sessions <= 0 or trading_session_open(moment) or throttled(VALUATION_BACKFILL):
        return total if ran else None
    sessions = await repository.list_market_trading_dates(
        None, as_of=today, limit=backfill_sessions
    )
    covered = await repository.list_fetch_coverage(
        SOURCE, VALUATION_DAILY, "*", [d.isoformat() for d in sessions]
    )
    todo = [d for d in sessions if d.isoformat() not in covered][:backfill_per_tick]  # newest first
    for index, day in enumerate(todo):
        ran = True
        if index and request_interval:
            await asyncio.sleep(request_interval)
        parsed, new, failures = await _fetch_valuation_day(
            repository,
            twse,
            tpex,
            day,
            available_at=stamped_close(day),
            fetch_attempts=fetch_attempts,
            request_interval=request_interval,
        )
        total += new
        if failures:
            last_failure[VALUATION_BACKFILL] = moment
            break
        await _record(repository, VALUATION_DAILY, day.isoformat(), parsed, moment)
        logger.info("valuation backfill %s: %s rows, %s new", day, parsed, new)
    return total if ran else None


# --- monthly revenue history ----------------------------------------------------------------


async def _refresh_revenue_history(
    repository: Any,
    twse: _HasClient,
    *,
    moment: datetime,
    months: int,
    per_tick: int,
    last_failure: dict[str, datetime],
    fetch_attempts: int,
    mops_interval: float,
) -> int | None:
    wanted = closed_revenue_months(moment, months)
    covered = await repository.list_fetch_coverage(SOURCE, REVENUE_HISTORY, "*", wanted)
    todo = [period for period in wanted if period not in covered][:per_tick]
    if not todo:
        return None
    total = 0
    for period in todo:
        stamped = fnd.revenue_deadline(period)
        rows: list = []
        try:
            for index, (market, suffix) in enumerate((("sii", ".TW"), ("otc", ".TWO"))):
                if index:
                    await asyncio.sleep(mops_interval)
                content = await _get_bytes(
                    twse.client, fnd.mops_revenue_url(market, period), attempts=fetch_attempts
                )
                parsed = fnd.parse_mops_revenue_html(
                    fnd.decode_mops_revenue(content),
                    period=period,
                    suffix=suffix,
                    available_at=stamped,
                )
                if len(parsed) < MIN_REVENUE_ROWS:
                    raise RuntimeError(f"revenue {market} {period} looks incomplete: {len(parsed)}")
                rows.extend(parsed)
            new = await repository.insert_missing_monthly_revenue(rows)
        except Exception:
            last_failure[REVENUE_HISTORY] = moment
            logger.exception("revenue history %s failed", period)
            await _rollback(repository)
            break
        total += new
        await _record(repository, REVENUE_HISTORY, period, len(rows), moment)
        logger.info("revenue history %s: %s rows, %s new", period, len(rows), new)
        await asyncio.sleep(mops_interval)
    return total


# --- quarterly financials ----------------------------------------------------------------


async def _fetch_statements(
    client: httpx.AsyncClient, income_url: str, balance_url: str, *, attempts: int, interval: float
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    income = await fetch_json_list(client, income_url, attempts=attempts)
    await asyncio.sleep(interval)
    balance = await fetch_json_list(client, balance_url, attempts=attempts)
    return income, balance


async def _refresh_financials_forward(
    repository: Any,
    twse: _HasClient,
    tpex: _HasClient,
    *,
    moment: datetime,
    slot: str,
    last_failure: dict[str, datetime],
    fetch_attempts: int,
    request_interval: float,
) -> int:
    """Latest quarter, every statement kind. The two general-industry (``ci``) statements
    are required (a failure there retries the slot); a failing financial-sector kind is
    logged and left to the next slot."""
    rows: list = []
    required_failed = False
    markets = (
        (twse, ".TW", fnd.TWSE_FINANCIALS_SOURCE, fnd.TWSE_INCOME_URL, fnd.TWSE_BALANCE_URL),
        (tpex, ".TWO", fnd.TPEX_FINANCIALS_SOURCE, fnd.TPEX_INCOME_URL, fnd.TPEX_BALANCE_URL),
    )
    for provider, suffix, source, income_url, balance_url in markets:
        for kind in fnd.STATEMENT_KINDS:
            try:
                income, balance = await _fetch_statements(
                    provider.client,
                    income_url.format(kind=kind),
                    balance_url.format(kind=kind),
                    attempts=fetch_attempts,
                    interval=request_interval,
                )
                built = fnd.build_quarterly_financials(
                    income, balance, suffix=suffix, source=source, available_at=moment
                )
                if kind == "ci" and not built:
                    raise RuntimeError(f"{source} ci statements came back empty")
                rows.extend(built)
            except Exception:
                logger.exception("quarterly financials %s %s failed", source, kind)
                if kind == "ci":
                    required_failed = True
            await asyncio.sleep(request_interval)
    written = 0
    try:
        written = await repository.upsert_quarterly_financials(rows) if rows else 0
    except Exception:
        required_failed = True
        logger.exception("quarterly financials upsert failed")
        await _rollback(repository)
    if required_failed:
        last_failure[FINANCIALS_FORWARD] = moment
        return written
    last_failure.pop(FINANCIALS_FORWARD, None)
    await _record(repository, FINANCIALS_FORWARD, slot, len(rows), moment)
    logger.info("quarterly financials: %s rows, %s new (%s)", len(rows), written, slot)
    return written


async def _refresh_financials_history(
    repository: Any,
    twse: _HasClient,
    *,
    moment: datetime,
    quarters: int,
    per_tick: int,
    last_failure: dict[str, datetime],
    fetch_attempts: int,
    mops_interval: float,
) -> int | None:
    wanted = closed_quarters(moment, quarters)
    labels = [f"{year}Q{quarter}" for year, quarter in wanted]
    covered = await repository.list_fetch_coverage(SOURCE, FINANCIALS_HISTORY, "*", labels)
    todo = [(y, q) for (y, q), label in zip(wanted, labels, strict=True) if label not in covered]
    if not todo:
        return None
    total = 0
    for year, quarter in todo[:per_tick]:
        label = f"{year}Q{quarter}"
        rows: list = []
        try:
            for market, suffix in (("sii", ".TW"), ("otc", ".TWO")):
                form = fnd.mops_statement_form(market, year, quarter)
                income = fnd.parse_mops_statement_html(
                    await _post_text(
                        twse.client, fnd.MOPS_INCOME_URL, form, attempts=fetch_attempts
                    )
                )
                await asyncio.sleep(mops_interval)
                balance = fnd.parse_mops_statement_html(
                    await _post_text(
                        twse.client, fnd.MOPS_BALANCE_URL, form, attempts=fetch_attempts
                    )
                )
                await asyncio.sleep(mops_interval)
                rows.extend(
                    fnd.build_quarterly_financials(
                        income,
                        balance,
                        suffix=suffix,
                        source=fnd.MOPS_FINANCIALS_SOURCE,
                        fiscal=(year, quarter),
                    )
                )
            new = await repository.insert_missing_quarterly_financials(rows)
        except Exception:
            last_failure[FINANCIALS_HISTORY] = moment
            logger.exception("financials history %s failed", label)
            await _rollback(repository)
            break
        total += new
        await _record(repository, FINANCIALS_HISTORY, label, len(rows), moment)
        logger.info("financials history %s: %s rows, %s new", label, len(rows), new)
    return total


# --- entry point ------------------------------------------------------------------------------


async def refresh_fundamentals_data(
    repository: Any,
    twse: _HasClient,
    tpex: _HasClient,
    *,
    now: datetime | None = None,
    attempts: dict[str, datetime] | None = None,
    valuation_enabled: bool = True,
    valuation_window: int = 5,
    valuation_backfill_sessions: int = 250,
    revenue_history_enabled: bool = True,
    revenue_backfill_months: int = 36,
    financials_enabled: bool = True,
    financials_backfill_quarters: int = 12,
    backfill_per_tick: int = 3,
    retry_after: timedelta = RETRY_AFTER,
    fetch_attempts: int = 3,
    request_interval: float = 0.35,
    mops_interval: float = 3.0,
) -> dict[str, int]:
    """Run every dataset that is due; returns rows written per dataset that did any work."""
    moment = now or datetime.now(UTC)
    last_failure = attempts if attempts is not None else {}
    written: dict[str, int] = {}

    def throttled(dataset: str) -> bool:
        failed_at = last_failure.get(dataset)
        return failed_at is not None and moment - failed_at < retry_after

    if valuation_enabled:
        result = await _refresh_valuation(
            repository,
            twse,
            tpex,
            moment=moment,
            window=valuation_window,
            backfill_sessions=valuation_backfill_sessions,
            backfill_per_tick=backfill_per_tick,
            last_failure=last_failure,
            throttled=throttled,
            fetch_attempts=fetch_attempts,
            request_interval=request_interval,
        )
        if result is not None:
            written[VALUATION_DAILY] = result

    if financials_enabled and not throttled(FINANCIALS_FORWARD):
        slot = financials_slot(moment)
        done = await repository.list_fetch_coverage(SOURCE, FINANCIALS_FORWARD, "*", [slot])
        if slot not in done:
            written[FINANCIALS_FORWARD] = await _refresh_financials_forward(
                repository,
                twse,
                tpex,
                moment=moment,
                slot=slot,
                last_failure=last_failure,
                fetch_attempts=fetch_attempts,
                request_interval=request_interval,
            )

    if trading_session_open(moment):
        return written  # the MOPS backfills wait for the evening / weekend
    if revenue_history_enabled and revenue_backfill_months > 0 and not throttled(REVENUE_HISTORY):
        result = await _refresh_revenue_history(
            repository,
            twse,
            moment=moment,
            months=revenue_backfill_months,
            per_tick=max(1, backfill_per_tick - 1),
            last_failure=last_failure,
            fetch_attempts=fetch_attempts,
            mops_interval=mops_interval,
        )
        if result is not None:
            written[REVENUE_HISTORY] = result
    if (
        financials_enabled
        and financials_backfill_quarters > 0
        and not throttled(FINANCIALS_HISTORY)
    ):
        result = await _refresh_financials_history(
            repository,
            twse,
            moment=moment,
            quarters=financials_backfill_quarters,
            per_tick=1,
            last_failure=last_failure,
            fetch_attempts=fetch_attempts,
            mops_interval=mops_interval,
        )
        if result is not None:
            written[FINANCIALS_HISTORY] = result
    return written
