"""Stores each trading day's whole-market snapshot, one request set per day."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol

from quant_signal.application.data_sync import (
    DataSyncReport,
    _fetched_after,
    _retry_empty,
)
from quant_signal.domain.models import FetchCoverage, MarketStockDay

logger = logging.getLogger(__name__)

MARKET_STOCK_DATASET = "market_stock_day"
MarketCode = Literal["TW", "TWO"]


class MarketSnapshotProvider(Protocol):
    async def fetch_market_stock_day(self, trading_date: date) -> list[MarketStockDay]: ...


class MarketSnapshotRepository(Protocol):
    async def upsert_market_stock_days(self, rows: list[MarketStockDay]) -> int: ...

    async def list_fetch_coverage(
        self, source: str, dataset: str, symbol: str, periods: list[str]
    ) -> dict[str, FetchCoverage]: ...

    async def record_fetch_coverage(self, entries: list[FetchCoverage]) -> None: ...


class MarketSnapshotSyncService:
    """Fetches only the weekdays not yet stored after they ended.

    A day fetched on the day itself is refetched the next day, because the
    exchanges publish institutional and credit reports after the close.
    Holidays are recorded as empty so they are asked for once. An empty day the
    ``sibling`` exchange (``(source, market)``, same trading calendar) has rows for is not
    a holiday but a failed fetch, and is retried with a backoff (see ``_retry_empty``).
    """

    def __init__(
        self,
        repository: MarketSnapshotRepository,
        provider: MarketSnapshotProvider,
        *,
        market: Literal["TW", "TWO"],
        source: str,
        sibling: tuple[str, MarketCode] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.market = market
        self.source = source
        self.sibling = sibling
        self._clock = clock or (lambda: datetime.now(UTC))

    async def sync(
        self,
        *,
        start: date,
        end: date,
        on_progress: Callable[[], Awaitable[None]] | None = None,
        progress_every: int = 40,
    ) -> DataSyncReport:
        started_at = self._clock()
        weekdays = [
            start + timedelta(days=offset)
            for offset in range((end - start).days + 1)
            if (start + timedelta(days=offset)).weekday() < 5
        ]
        pending = await pending_snapshot_days(
            self.repository,
            source=self.source,
            market=self.market,
            sibling=self.sibling,
            weekdays=weekdays,
            now=started_at,
        )
        stored = 0
        # Newest first: if the run is cut short, the most useful days exist.
        for index, day in enumerate(sorted(pending, reverse=True), start=1):
            rows = await self.provider.fetch_market_stock_day(day)
            stored += await self.repository.upsert_market_stock_days(rows)
            await self.repository.record_fetch_coverage(
                [
                    FetchCoverage(
                        source=self.source,
                        dataset=MARKET_STOCK_DATASET,
                        symbol=self.market,
                        period=day.isoformat(),
                        status="ok" if rows else "empty",
                        row_count=len(rows),
                        fetched_at=self._clock(),
                    )
                ]
            )
            if on_progress is not None and index % progress_every == 0 and index < len(pending):
                # Lets derived data (the market score) appear while a long fill is still running.
                await on_progress()
            if index % 20 == 0:
                logger.info(
                    "%s market snapshot: %s/%s days (%s)",
                    self.market,
                    index,
                    len(pending),
                    day,
                )
        return DataSyncReport(
            provider=self.source,
            started_at=started_at,
            completed_at=self._clock(),
            requested_start=start,
            requested_end=end,
            bars_received=0,
            bars_inserted=0,
            market_observations_inserted=stored,
            periods_fetched=len(pending),
            periods_skipped=len(weekdays) - len(pending),
            notes=["Whole-market official quotes, institutional flows and credit balances."],
        )


async def pending_snapshot_days(
    repository: MarketSnapshotRepository,
    *,
    source: str,
    market: MarketCode,
    sibling: tuple[str, MarketCode] | None,
    weekdays: list[date],
    now: datetime,
    repairs_only: bool = False,
) -> list[date]:
    """Weekdays to (re)fetch: not fetched after they ended, or a suspicious ``empty``.

    ``repairs_only`` returns just the suspicious empties (what the worker's repair check
    looks for; the ordinary pending days are the daily refresh's business).
    """
    periods = [day.isoformat() for day in weekdays]
    coverage = await repository.list_fetch_coverage(source, MARKET_STOCK_DATASET, market, periods)
    sibling_coverage = (
        await repository.list_fetch_coverage(sibling[0], MARKET_STOCK_DATASET, sibling[1], periods)
        if sibling is not None
        else {}
    )
    pending: list[date] = []
    for day in weekdays:
        entry = coverage.get(day.isoformat())
        if _retry_empty(entry, sibling_coverage.get(day.isoformat()), day, now):
            pending.append(day)
        elif not repairs_only and not _fetched_after(entry, day):
            pending.append(day)
    return pending
