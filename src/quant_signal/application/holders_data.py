"""Scheduled refresh of the whole-market holder datasets (sync worker tick).

- ``tdcc_shareholding``: TDCC publishes the weekly 集保戶股權分散表 (as of Friday) on the
  weekend and the open-data file only ever holds the latest week. We poll twice a day
  (slots ``YYYY-MM-DD-am`` from 08:00, ``-pm`` from 17:00, any weekday) and upsert; a
  new ``data_date`` simply adds rows, a repeat adds nothing. History grows from the day
  this was switched on.
- ``foreign_holding``: TWSE MI_QFIIS + TPEx qfii for each of the last ``foreign_days``
  weekdays (today only from 17:00); a day is fetched once and recorded in
  ``fetch_coverage`` (``empty`` for holidays).

Like ``official_events`` a failure is logged, remembered in memory and retried after
``retry_after``; nothing here raises into the price sync.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol

import httpx

from quant_signal.application.data_sync import TAIPEI
from quant_signal.application.source_guard import Backoff, MissingSources, SourceBlocked, retrying
from quant_signal.domain.models import FetchCoverage
from quant_signal.infrastructure.providers import holders as src

logger = logging.getLogger(__name__)

SOURCE = "holders_data"
TDCC_SHAREHOLDING = "tdcc_shareholding"
FOREIGN_HOLDING = "foreign_holding"
RETRY_AFTER = timedelta(minutes=10)
MIN_TDCC_ROWS = 1500  # ~1,900 listed + OTC common stocks
MIN_FOREIGN_ROWS = 400  # TWSE alone has ~1,000 common stocks, TPEx ~800
FOREIGN_EXCHANGES = ("twse", "tpex")
FOREIGN_PUBLISHED = time(21, 0)  # Taipei: the day's foreign-holding tables are out by then
FINAL_HOUR = 22  # Taipei: after this a still-empty exchange counts as "not published today"


def foreign_dataset(exchange: str) -> str:
    """Coverage per exchange: TWSE and TPEx publish at different times."""
    return f"{FOREIGN_HOLDING}_{exchange}"


def foreign_available_at(day: date, moment: datetime) -> datetime:
    """Stamp for a day's foreign-holding rows: the fetch time for the live same-day fetch,
    otherwise the day's publication time (21:00 Taipei, never later than the fetch), so a
    snapshot recomputed for a past date can see rows we only fetched afterwards."""
    if day >= moment.astimezone(TAIPEI).date():
        return moment
    return min(moment, datetime.combine(day, FOREIGN_PUBLISHED, TAIPEI))


class _HasClient(Protocol):
    client: httpx.AsyncClient


def tdcc_slot(now: datetime) -> str | None:
    local = now.astimezone(TAIPEI)
    if local.hour >= 17:
        return f"{local.date().isoformat()}-pm"
    if local.hour >= 8:
        return f"{local.date().isoformat()}-am"
    return None


def foreign_days(now: datetime, count: int) -> list[date]:
    """The last ``count`` weekdays, oldest first; today counts only from 17:00."""
    local = now.astimezone(TAIPEI)
    day = local.date()
    if local.hour < 17:
        day -= timedelta(days=1)
    days: list[date] = []
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


async def _get(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, str] | None,
    *,
    attempts: int,
    backoff: Backoff | None = None,
) -> httpx.Response:
    async def call() -> httpx.Response:
        response = await client.get(url, params=params, headers={"Accept": "*/*"})
        response.raise_for_status()
        return response

    return await retrying(backoff, call, url=url, attempts=attempts)


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


async def _refresh_tdcc(
    repository: Any,
    twse: _HasClient,
    *,
    moment: datetime,
    fetch_attempts: int,
    backoff: Backoff | None = None,
) -> tuple[int, int]:
    industries = await repository.list_market_industries()
    markets = {item.symbol.split(".")[0]: item.symbol for item in industries}
    if not markets:
        raise RuntimeError("industry classification not loaded yet; cannot map TDCC codes")
    response = await _get(twse.client, src.TDCC_URL, None, attempts=fetch_attempts, backoff=backoff)
    rows = src.parse_tdcc_distribution(
        response.content.decode("utf-8-sig"), markets=markets, available_at=moment
    )
    if len(rows) < MIN_TDCC_ROWS:
        raise RuntimeError(f"tdcc shareholding looks incomplete: {len(rows)} rows")
    return len(rows), await repository.upsert_shareholding_distribution(rows)


async def _refresh_foreign_day(
    repository: Any,
    twse: _HasClient,
    tpex: _HasClient,
    day: date,
    *,
    exchanges: tuple[str, ...] = FOREIGN_EXCHANGES,
    moment: datetime,
    fetch_attempts: int,
    request_interval: float,
    backoff: Backoff | None = None,
) -> tuple[dict[str, int], int, list[str]]:
    """Returns (rows parsed per exchange, rows written, exchanges that failed) for one day."""
    stamp = foreign_available_at(day, moment)
    plan = {
        "twse": (twse, src.TWSE_QFIIS_URL, src.twse_qfiis_params(day), src.parse_twse_qfiis),
        "tpex": (tpex, src.TPEX_QFII_URL, src.tpex_qfii_params(day), src.parse_tpex_qfii),
    }
    counts: dict[str, int] = {}
    failed: list[str] = []
    written = 0
    for index, exchange in enumerate(exchanges):
        provider, url, params, parse = plan[exchange]
        if index and request_interval:
            await asyncio.sleep(request_interval)
        try:
            payload = await _get_json(
                provider.client, url, params, attempts=fetch_attempts, backoff=backoff
            )
            rows = [row for row in parse(payload, available_at=stamp) if row.trading_date == day]
            if rows and len(rows) < MIN_FOREIGN_ROWS // 2:
                raise RuntimeError(f"foreign holding looks incomplete: {len(rows)} rows ({url})")
            counts[exchange] = len(rows)
            written += await repository.upsert_foreign_holding(rows) if rows else 0
        except SourceBlocked:
            failed.append(exchange)
            logger.warning("foreign holding source unavailable: %s %s", url, day)
            await _rollback(repository)
        except Exception:
            failed.append(exchange)
            logger.exception("foreign holding request failed: %s %s", url, day)
            await _rollback(repository)
    return counts, written, failed


async def refresh_holders_data(
    repository: Any,
    twse: _HasClient,
    tpex: _HasClient,
    *,
    now: datetime | None = None,
    attempts: dict[str, datetime] | None = None,
    tdcc_enabled: bool = True,
    foreign_enabled: bool = True,
    foreign_window: int = 5,
    retry_after: timedelta = RETRY_AFTER,
    fetch_attempts: int = 3,
    request_interval: float = 0.35,
) -> dict[str, int]:
    """Run every holder dataset that is due; returns rows written per dataset."""
    moment = now or datetime.now(UTC)
    last_failure = attempts if attempts is not None else {}
    written: dict[str, int] = {}

    def throttled(dataset: str) -> bool:
        failed_at = last_failure.get(dataset)
        return failed_at is not None and moment - failed_at < retry_after

    backoff = Backoff(last_failure, moment, retry_after)
    tracker = MissingSources(
        repository, source=SOURCE, moment=moment, last_failure=last_failure, retry_after=retry_after
    )
    slot = tdcc_slot(moment) if tdcc_enabled else None
    # A fresh deploy has no TDCC history: do not wait for the next 08:00 / 17:00 slot.
    first_run = tdcc_enabled and not await repository.has_shareholding_distribution()
    if first_run and slot is None:
        slot = f"{moment.astimezone(TAIPEI).date().isoformat()}-first"
    if slot and not throttled(TDCC_SHAREHOLDING):
        done = await repository.list_fetch_coverage(SOURCE, TDCC_SHAREHOLDING, "*", [slot])
        if slot not in done or first_run:
            try:
                parsed, new = await _refresh_tdcc(
                    repository, twse, moment=moment, fetch_attempts=fetch_attempts, backoff=backoff
                )
            except Exception:
                last_failure[TDCC_SHAREHOLDING] = moment
                logger.exception("tdcc shareholding refresh failed")
                await _rollback(repository)
            else:
                last_failure.pop(TDCC_SHAREHOLDING, None)
                written[TDCC_SHAREHOLDING] = new
                await _record(repository, TDCC_SHAREHOLDING, slot, parsed, moment)
                logger.info("tdcc shareholding: %s rows, %s new (%s)", parsed, new, slot)

    if foreign_enabled and not throttled(FOREIGN_HOLDING):
        local = moment.astimezone(TAIPEI)
        today = local.date()
        total = 0
        worked = False
        for day in foreign_days(moment, foreign_window):
            key = day.isoformat()
            past = day < today
            final = past or local.hour >= FINAL_HOUR
            covered = {
                exchange: await repository.list_fetch_coverage(
                    SOURCE, foreign_dataset(exchange), "*", [key]
                )
                for exchange in FOREIGN_EXCHANGES
            }
            due = tuple(
                [
                    exchange
                    for exchange in FOREIGN_EXCHANGES
                    if key not in covered[exchange]
                    and await tracker.due(foreign_dataset(exchange), key, final=final)
                ]
            )
            if not due:
                continue
            if worked and request_interval:
                await asyncio.sleep(request_interval)
            worked = True
            counts, new, failed = await _refresh_foreign_day(
                repository,
                twse,
                tpex,
                day,
                exchanges=due,
                moment=moment,
                fetch_attempts=fetch_attempts,
                request_interval=request_interval,
                backoff=backoff,
            )
            total += new
            day_has_rows = any(counts.values()) or any(
                key in covered[exchange] and covered[exchange][key].status == "ok"
                for exchange in FOREIGN_EXCHANGES
            )
            for exchange, rows in counts.items():
                if failed and not rows:
                    continue  # the exchange that failed may be why this one looks empty
                await tracker.settle(
                    foreign_dataset(exchange),
                    key,
                    key,
                    rows,
                    day_has_rows=day_has_rows,
                    past=past,
                    final=final,
                )
            logger.info("foreign holding %s: rows %s, %s new", day, counts, new)
            if failed:
                last_failure[FOREIGN_HOLDING] = moment
                break  # retry later; do not keep hitting an exchange that is failing
        if worked:
            written[FOREIGN_HOLDING] = total
    return written
