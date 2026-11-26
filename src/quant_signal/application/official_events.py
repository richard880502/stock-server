"""Scheduled refresh of the official structured market data.

Called from the sync worker's tick. Each dataset has a *slot* (an idempotent period
label such as ``2026-11-26-pm`` or ``2026-11-26T09:30``); a slot is fetched at most
once, recorded in ``fetch_coverage``, so a restart never refetches. A failed fetch is
logged, remembered in memory, and retried after ``retry_after`` -- it never raises
into the price sync.

Schedule (Asia/Taipei):

- ``risk_flags`` and ``ex_rights``: weekdays, once after 17:00 and once more from 08:00
  the next morning (slots ``YYYY-MM-DD-pm`` / ``YYYY-MM-DD-am``).
- ``material``: weekdays 07:00-22:00, every ``material_minutes`` (default 30).
- ``monthly_revenue``: daily on days 1-15 of the month, weekly (ISO week) otherwise.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

from quant_signal.application.data_sync import TAIPEI
from quant_signal.domain.models import FetchCoverage
from quant_signal.infrastructure.providers import official_events as src
from quant_signal.infrastructure.providers.industry import fetch_json_list

logger = logging.getLogger(__name__)

SOURCE = "official_events"
RISK_FLAGS = "risk_flags"
EX_RIGHTS = "ex_rights"
MATERIAL = "material"
MONTHLY_REVENUE = "monthly_revenue"
RETRY_AFTER = timedelta(minutes=10)
MIN_REVENUE_ROWS = 200  # TWSE ~1,090 + TPEx ~900 companies publish each month


class _HasClient(Protocol):
    client: httpx.AsyncClient


# --- slots ------------------------------------------------------------------------


def twice_daily_slot(now: datetime) -> str | None:
    """Weekdays: ``D-pm`` from 17:00, ``D-am`` from 08:00; otherwise nothing is due."""
    local = now.astimezone(TAIPEI)
    if local.weekday() >= 5:
        return None
    if local.hour >= 17:
        return f"{local.date().isoformat()}-pm"
    if local.hour >= 8:
        return f"{local.date().isoformat()}-am"
    return None


def material_slot(now: datetime, interval_minutes: int = 30) -> str | None:
    """Weekdays 07:00-22:00: the start of the current ``interval_minutes`` window."""
    local = now.astimezone(TAIPEI)
    minutes = local.hour * 60 + local.minute
    if local.weekday() >= 5 or not 7 * 60 <= minutes <= 22 * 60:
        return None
    start = minutes // interval_minutes * interval_minutes
    return f"{local.date().isoformat()}T{start // 60:02d}:{start % 60:02d}"


def revenue_slot(now: datetime) -> str:
    """Daily while companies are still filing (days 1-15), weekly afterwards."""
    local = now.astimezone(TAIPEI)
    if local.day <= 15:
        return local.date().isoformat()
    year, week, _ = local.isocalendar()
    return f"{year}-W{week:02d}"


# --- fetching ---------------------------------------------------------------------


async def _fetch_all(
    endpoints: list[tuple[_HasClient, str, Callable[[Any], list]]],
    *,
    fetch_attempts: int,
    request_interval: float,
) -> tuple[list, int]:
    """Parse every endpoint independently; returns (rows, failed endpoint count)."""
    rows: list = []
    failures = 0
    for index, (provider, url, parse) in enumerate(endpoints):
        if index and request_interval:
            await asyncio.sleep(request_interval)
        try:
            payload = await fetch_json_list(provider.client, url, attempts=fetch_attempts)
            rows.extend(parse(payload))
        except Exception:
            failures += 1
            logger.exception("official data request failed: %s", url)
    return rows, failures


async def refresh_official_data(
    repository: Any,
    twse: _HasClient,
    tpex: _HasClient,
    *,
    now: datetime | None = None,
    attempts: dict[str, datetime] | None = None,
    material_minutes: int = 30,
    retry_after: timedelta = RETRY_AFTER,
    fetch_attempts: int = 3,
    request_interval: float = 0.35,
) -> dict[str, int]:
    """Run every dataset that is due; returns rows written per dataset that ran."""
    moment = now or datetime.now(UTC)
    last_failure = attempts if attempts is not None else {}
    today = moment.astimezone(TAIPEI).date()

    def risk_endpoints():
        return [
            (twse, src.TWSE_PUNISH_URL, lambda p: src.parse_twse_punish(p, available_at=moment)),
            (
                tpex,
                src.TPEX_DISPOSAL_URL,
                lambda p: src.parse_tpex_disposal(p, available_at=moment),
            ),
            (twse, src.TWSE_NOTICE_URL, lambda p: src.parse_twse_notice(p, available_at=moment)),
            (
                twse,
                src.TWSE_NOTETRANS_URL,
                lambda p: src.parse_twse_notetrans(p, available_at=moment, as_of=today),
            ),
            (tpex, src.TPEX_WARNING_URL, lambda p: src.parse_tpex_warning(p, available_at=moment)),
            (
                tpex,
                src.TPEX_WARNING_NOTE_URL,
                lambda p: src.parse_tpex_warning_note(p, available_at=moment),
            ),
        ]

    plan: list[tuple[str, str | None, list, Callable, int]] = [
        (RISK_FLAGS, twice_daily_slot(moment), risk_endpoints(), repository.upsert_risk_flags, 0),
        (
            EX_RIGHTS,
            twice_daily_slot(moment),
            [
                (
                    twse,
                    src.TWSE_EXRIGHTS_URL,
                    lambda p: src.parse_twse_exrights(p, available_at=moment),
                ),
                (
                    tpex,
                    src.TPEX_EXRIGHTS_URL,
                    lambda p: src.parse_tpex_exrights(p, available_at=moment),
                ),
            ],
            repository.upsert_market_events,
            0,
        ),
        (
            MATERIAL,
            material_slot(moment, material_minutes),
            [
                (
                    twse,
                    src.TWSE_MATERIAL_URL,
                    lambda p: src.parse_twse_material(p, available_at=moment),
                ),
                (
                    tpex,
                    src.TPEX_MATERIAL_URL,
                    lambda p: src.parse_tpex_material(p, available_at=moment),
                ),
            ],
            repository.upsert_market_events,
            0,
        ),
        (
            MONTHLY_REVENUE,
            revenue_slot(moment),
            [
                (
                    twse,
                    src.TWSE_REVENUE_URL,
                    lambda p: src.parse_twse_revenue(p, available_at=moment),
                ),
                (
                    tpex,
                    src.TPEX_REVENUE_URL,
                    lambda p: src.parse_tpex_revenue(p, available_at=moment),
                ),
            ],
            repository.upsert_monthly_revenue,
            MIN_REVENUE_ROWS,
        ),
    ]

    written: dict[str, int] = {}
    for dataset, slot, endpoints, upsert, min_rows in plan:
        if slot is None:
            continue
        failed_at = last_failure.get(dataset)
        if failed_at is not None and moment - failed_at < retry_after:
            continue
        done = await repository.list_fetch_coverage(SOURCE, dataset, "*", [slot])
        if slot in done:
            continue
        try:
            rows, failures = await _fetch_all(
                endpoints, fetch_attempts=fetch_attempts, request_interval=request_interval
            )
            if len(rows) < min_rows and min_rows:
                raise RuntimeError(f"{dataset} looks incomplete: {len(rows)} rows")
            # Store whatever parsed even when a sibling endpoint failed: upserts are
            # idempotent, and the slot stays open so the failed side is retried.
            written[dataset] = await upsert(rows) if rows else 0
            if failures:
                raise RuntimeError(f"{dataset}: {failures} endpoint(s) failed")
        except Exception:
            last_failure[dataset] = moment
            logger.exception("official %s refresh failed", dataset)
            rollback = getattr(getattr(repository, "session", None), "rollback", None)
            if rollback is not None:  # a failed statement poisons the shared session
                await rollback()
            continue
        last_failure.pop(dataset, None)
        await repository.record_fetch_coverage(
            [
                FetchCoverage(
                    source=SOURCE,
                    dataset=dataset,
                    symbol="*",
                    period=slot,
                    status="ok" if rows else "empty",
                    row_count=len(rows),
                    fetched_at=moment,
                )
            ]
        )
        logger.info(
            "official %s refreshed: %s rows, %s new (%s)",
            dataset,
            len(rows),
            written[dataset],
            slot,
        )
    return written
