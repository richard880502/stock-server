"""Stores each trading day's whole-market snapshot, one request set per day."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol

from quant_signal.application.data_sync import (
    DataSyncReport,
    _fetched_after,
)
from quant_signal.domain.models import FetchCoverage, MarketStockDay

logger = logging.getLogger(__name__)

MARKET_STOCK_DATASET = "market_stock_day"


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
    Holidays are recorded as empty so they are asked for once.
    """

    def __init__(
        self,
        repository: MarketSnapshotRepository,
        provider: MarketSnapshotProvider,
        *,
        market: Literal["TW", "TWO"],
        source: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.market = market
        self.source = source
        self._clock = clock or (lambda: datetime.now(UTC))

    async def sync(self, *, start: date, end: date) -> DataSyncReport:
        started_at = self._clock()
        weekdays = [
            start + timedelta(days=offset)
            for offset in range((end - start).days + 1)
            if (start + timedelta(days=offset)).weekday() < 5
        ]
        coverage = await self.repository.list_fetch_coverage(
            self.source,
            MARKET_STOCK_DATASET,
            self.market,
            [day.isoformat() for day in weekdays],
        )
        pending = [
            day
            for day in weekdays
            if not _fetched_after(coverage.get(day.isoformat()), day)
        ]
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
