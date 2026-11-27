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
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import httpx

from quant_signal.application.data_sync import TAIPEI
from quant_signal.domain.models import FetchCoverage
from quant_signal.infrastructure.providers import holders as src

logger = logging.getLogger(__name__)

SOURCE = "holders_data"
TDCC_SHAREHOLDING = "tdcc_shareholding"
FOREIGN_HOLDING = "foreign_holding"
RETRY_AFTER = timedelta(minutes=10)
MIN_TDCC_ROWS = 1500  # ~1,900 listed + OTC common stocks
MIN_FOREIGN_ROWS = 400  # TWSE alone has ~1,000 common stocks, TPEx ~800


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
    client: httpx.AsyncClient, url: str, params: dict[str, str] | None, *, attempts: int
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.get(url, params=params, headers={"Accept": "*/*"})
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            last_error = exc
            await asyncio.sleep(min(1.5 * (2**attempt), 15))
    raise RuntimeError(f"holders request failed after retries: {url}") from last_error


async def _get_json(
    client: httpx.AsyncClient, url: str, params: dict[str, str], *, attempts: int
) -> Any:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.get(url, params=params, headers={"Accept": "*/*"})
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            await asyncio.sleep(min(1.5 * (2**attempt), 15))
    raise RuntimeError(f"holders request failed after retries: {url}") from last_error


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
    repository: Any, twse: _HasClient, *, moment: datetime, fetch_attempts: int
) -> tuple[int, int]:
    industries = await repository.list_market_industries()
    markets = {item.symbol.split(".")[0]: item.symbol for item in industries}
    if not markets:
        raise RuntimeError("industry classification not loaded yet; cannot map TDCC codes")
    response = await _get(twse.client, src.TDCC_URL, None, attempts=fetch_attempts)
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
    moment: datetime,
    fetch_attempts: int,
    request_interval: float,
) -> tuple[int, int, int]:
    """Returns (rows parsed, rows written, failed exchange count) for one day."""
    parsed = written = failures = 0
    plan = (
        (twse, src.TWSE_QFIIS_URL, src.twse_qfiis_params(day), src.parse_twse_qfiis),
        (tpex, src.TPEX_QFII_URL, src.tpex_qfii_params(day), src.parse_tpex_qfii),
    )
    for index, (provider, url, params, parse) in enumerate(plan):
        if index and request_interval:
            await asyncio.sleep(request_interval)
        try:
            payload = await _get_json(provider.client, url, params, attempts=fetch_attempts)
            rows = [row for row in parse(payload, available_at=moment) if row.trading_date == day]
            if rows and len(rows) < MIN_FOREIGN_ROWS // 2:
                raise RuntimeError(f"foreign holding looks incomplete: {len(rows)} rows ({url})")
            parsed += len(rows)
            written += await repository.upsert_foreign_holding(rows) if rows else 0
        except Exception:
            failures += 1
            logger.exception("foreign holding request failed: %s %s", url, day)
            await _rollback(repository)
    return parsed, written, failures


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

    slot = tdcc_slot(moment) if tdcc_enabled else None
    if slot and not throttled(TDCC_SHAREHOLDING):
        done = await repository.list_fetch_coverage(SOURCE, TDCC_SHAREHOLDING, "*", [slot])
        if slot not in done:
            try:
                parsed, new = await _refresh_tdcc(
                    repository, twse, moment=moment, fetch_attempts=fetch_attempts
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
        today = moment.astimezone(TAIPEI).date()
        days = foreign_days(moment, foreign_window)
        done = await repository.list_fetch_coverage(
            SOURCE, FOREIGN_HOLDING, "*", [day.isoformat() for day in days]
        )
        pending = [day for day in days if day.isoformat() not in done]
        total = 0
        for index, day in enumerate(pending):
            if index and request_interval:
                await asyncio.sleep(request_interval)
            parsed, new, failures = await _refresh_foreign_day(
                repository,
                twse,
                tpex,
                day,
                moment=moment,
                fetch_attempts=fetch_attempts,
                request_interval=request_interval,
            )
            total += new
            if failures:
                last_failure[FOREIGN_HOLDING] = moment
                break  # retry later; do not keep hitting an exchange that is failing
            if parsed == 0 and day >= today:
                last_failure[FOREIGN_HOLDING] = moment  # not published yet
                break
            await _record(repository, FOREIGN_HOLDING, day.isoformat(), parsed, moment)
            logger.info("foreign holding %s: %s rows, %s new", day, parsed, new)
        if pending:
            written[FOREIGN_HOLDING] = total
    return written
