"""Shared failure handling for the official-data side feeds.

Two concerns live here because every feed needs them identically:

* ``Backoff``: the first timeout / connection error / refusal / block page from a host ends
  the requests to that host for the rest of the pass and, through the feed's retry-after map,
  for the following ticks. Without it a hanging exchange costs ``urls x attempts x timeout``
  (27 minutes for the quarterly financials alone) in front of the price sync.
* ``MissingSources``: per-source coverage for days that several exchanges publish. A day is
  never recorded as done because one exchange answered; a source that is still empty while
  its siblings have rows is asked again on later ticks / days, a bounded number of times, and
  then recorded as empty with a warning.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from quant_signal.domain.models import FetchCoverage

logger = logging.getLogger(__name__)

TAIPEI = ZoneInfo("Asia/Taipei")
BLOCK_STATUSES = frozenset({301, 302, 303, 307, 308, 403, 429})
MAX_MISSING_DAYS = 3  # calendar days a still-empty source is asked about before giving up


class SourceBlocked(RuntimeError):
    """The host is not answering usefully right now; do not send it more requests this pass."""


class Backoff:
    """Per-host circuit breaker backed by the feed's retry-after map."""

    def __init__(
        self, failures: dict[str, datetime], moment: datetime, retry_after: timedelta
    ) -> None:
        self.failures = failures
        self.moment = moment
        self.retry_after = retry_after

    @staticmethod
    def _key(url: str) -> str:
        return f"blocked:{httpx.URL(url).host}"

    def blocked(self, url: str) -> bool:
        failed_at = self.failures.get(self._key(url))
        return failed_at is not None and self.moment - failed_at < self.retry_after

    def check(self, url: str) -> None:
        if self.blocked(url):
            raise SourceBlocked(f"backing off from {httpx.URL(url).host}")

    def trip(self, url: str) -> None:
        self.failures[self._key(url)] = self.moment


async def retrying(
    backoff: Backoff | None,
    call: Callable[[], Awaitable[Any]],
    *,
    url: str,
    attempts: int,
    what: str | None = None,
) -> Any:
    """Run ``call`` with retries on transient HTTP errors; stop at the first sign of a block.

    A timeout, connection error, refusal (403/429/redirect) or non-JSON answer raises
    ``SourceBlocked`` immediately; 5xx answers are retried with a short backoff.
    """
    if backoff is not None:
        backoff.check(url)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await call()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in BLOCK_STATUSES:
                last_error = exc
                await asyncio.sleep(min(1.5 * (2**attempt), 15))
                continue
            last_error = exc
        except (httpx.TransportError, ValueError) as exc:
            last_error = exc
        if backoff is not None:
            backoff.trip(url)
        raise SourceBlocked(f"request blocked or unreachable: {what or url}") from last_error
    raise RuntimeError(f"request failed after retries: {what or url}") from last_error


def taipei_day(moment: datetime) -> Any:
    return moment.astimezone(TAIPEI).date()


class MissingSources:
    """Bounded retries for a source that is empty while its siblings published."""

    def __init__(
        self,
        repository: Any,
        *,
        source: str,
        moment: datetime,
        last_failure: dict[str, datetime],
        retry_after: timedelta,
    ) -> None:
        self.repository = repository
        self.source = source
        self.moment = moment
        self.last_failure = last_failure
        self.retry_after = retry_after

    @staticmethod
    def _dataset(dataset: str) -> str:
        return f"{dataset}_miss"

    async def _state(self, dataset: str, key: str) -> FetchCoverage | None:
        found = await self.repository.list_fetch_coverage(
            self.source, self._dataset(dataset), "*", [key]
        )
        return found.get(key)

    async def given_up(self, dataset: str, key: str) -> bool:
        state = await self._state(dataset, key)
        return state is not None and state.row_count >= MAX_MISSING_DAYS

    def _throttle_key(self, dataset: str, key: str) -> str:
        return f"miss:{dataset}:{key}"

    async def due(self, dataset: str, key: str, *, final: bool) -> bool:
        """Should this still-missing source be asked now?

        Not when it was given up on, when it was asked less than ``retry_after`` ago, or (in
        the final phase) when it was already asked on this Taipei calendar day.
        """
        failed_at = self.last_failure.get(self._throttle_key(dataset, key))
        if failed_at is not None and self.moment - failed_at < self.retry_after:
            return False
        state = await self._state(dataset, key)
        if state is None:
            return True
        if state.row_count >= MAX_MISSING_DAYS:
            return False
        return not (final and taipei_day(state.fetched_at) == taipei_day(self.moment))

    async def miss(self, dataset: str, key: str, *, final: bool) -> bool:
        """Note one empty answer. Returns True when the source should now be given up on."""
        self.last_failure[self._throttle_key(dataset, key)] = self.moment
        if not final:
            return False
        state = await self._state(dataset, key)
        attempts = state.row_count if state else 0
        if state is None or taipei_day(state.fetched_at) != taipei_day(self.moment):
            attempts += 1
        await self.repository.record_fetch_coverage(
            [
                FetchCoverage(
                    source=self.source,
                    dataset=self._dataset(dataset),
                    symbol="*",
                    period=key,
                    status="empty",
                    row_count=attempts,
                    fetched_at=self.moment,
                )
            ]
        )
        return attempts >= MAX_MISSING_DAYS

    def recovered(self, dataset: str, key: str) -> None:
        self.last_failure.pop(self._throttle_key(dataset, key), None)

    async def _record(self, dataset: str, period: str, rows: int) -> None:
        await self.repository.record_fetch_coverage(
            [
                FetchCoverage(
                    source=self.source,
                    dataset=dataset,
                    symbol="*",
                    period=period,
                    status="ok" if rows else "empty",
                    row_count=rows,
                    fetched_at=self.moment,
                )
            ]
        )

    async def settle(
        self,
        dataset: str,
        period: str,
        key: str,
        rows: int,
        *,
        day_has_rows: bool,
        past: bool,
        final: bool,
        expect_data: bool = False,
    ) -> str:
        """Record what one source's answer established. Returns ``ok``, ``empty`` or ``pending``.

        ``day_has_rows``: some other source already has rows for this day, so it is a
        trading day and an empty answer means "not published yet", not "holiday".
        ``expect_data``: the slice must have rows (a closed fiscal quarter) so empty is never
        a holiday. A source that stays empty is only recorded as ``empty`` once it has been
        asked on ``MAX_MISSING_DAYS`` different days (or the day is a past holiday).
        """
        if rows:
            await self._record(dataset, period, rows)
            self.recovered(dataset, key)
            return "ok"
        if not (day_has_rows or expect_data):
            if past:
                await self._record(dataset, period, 0)  # a holiday: nothing to wait for
                return "empty"
            self.last_failure[self._throttle_key(dataset, key)] = self.moment
            return "pending"  # today and nothing published yet
        if await self.miss(dataset, key, final=final):
            logger.warning(
                "%s %s %s still empty after %s days although other sources published; "
                "recording it as empty",
                self.source,
                dataset,
                key,
                MAX_MISSING_DAYS,
            )
            await self._record(dataset, period, 0)
            return "empty"
        return "pending"
