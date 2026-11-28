"""Stores each trading day's whole-market snapshot, one request set per day."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from quant_signal.application.data_sync import (
    EMPTY_RETRY_AFTER,
    DataSyncReport,
    _fetched_after,
    _retry_empty,
)
from quant_signal.application.publication_time import (
    ETF_DAILY_PUBLISHED,
    publication_moment,
    stamp_available_at,
)
from quant_signal.domain.etf import EtfDay
from quant_signal.domain.models import FetchCoverage, MarketStockDay
from quant_signal.quant.paper_calendar import in_session, is_trading_day

logger = logging.getLogger(__name__)

MARKET_STOCK_DATASET = "market_stock_day"
# Own coverage dataset: ETF rows never change which days count as fetched for stocks.
ETF_DAY_DATASET = "etf_day"
# Days whose stock rows are already stored get their ETF rows by one extra request per
# market and day, at most this many per run, newest first. Days fetched by the stock loop
# carry their ETF rows in the same payload and are not limited by it.
ETF_BACKFILL_DAYS = 10
ETF_BACKFILL_WINDOW_DAYS = 21
MarketCode = Literal["TW", "TWO"]
TAIPEI = ZoneInfo("Asia/Taipei")


class MarketSnapshotProvider(Protocol):
    async def fetch_market_stock_day(self, trading_date: date) -> list[MarketStockDay]: ...


class MarketSnapshotRepository(Protocol):
    async def upsert_market_stock_days(self, rows: list[MarketStockDay]) -> int: ...

    async def upsert_etf_days(self, rows: list[EtfDay]) -> int: ...

    async def list_fetch_coverage(
        self, source: str, dataset: str, symbol: str, periods: list[str]
    ) -> dict[str, FetchCoverage]: ...

    async def record_fetch_coverage(self, entries: list[FetchCoverage]) -> None: ...


class EtfSnapshotProvider(Protocol):
    """What a provider adds to ``MarketSnapshotProvider`` to supply ETF rows."""

    async def fetch_market_day_rows(
        self, trading_date: date
    ) -> tuple[list[MarketStockDay], list[EtfDay]]: ...

    async def fetch_market_etf_day(self, trading_date: date) -> list[EtfDay]: ...


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
        # Providers that cannot supply ETF rows (test doubles) simply skip the ETF part.
        has_etfs = hasattr(provider, "fetch_market_day_rows") and hasattr(
            provider, "fetch_market_etf_day"
        )
        self._etf_provider: EtfSnapshotProvider | None = provider if has_etfs else None  # type: ignore[assignment]

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
        etf_stored = 0
        # Newest first: if the run is cut short, the most useful days exist.
        for index, day in enumerate(sorted(pending, reverse=True), start=1):
            etfs: list[EtfDay] | None = None
            if self._etf_provider is not None:
                rows, etfs = await self._etf_provider.fetch_market_day_rows(day)
            else:
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
            if etfs is not None:
                etf_stored += await self._store_etfs(day, etfs)
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
        backfilled = await self._backfill_etfs(end=end, now=started_at)
        notes = ["Whole-market official quotes, institutional flows and credit balances."]
        if self._etf_provider is not None:
            notes.append(
                f"ETF quotes: {etf_stored + backfilled[1]} rows "
                f"({backfilled[0]} extra day requests)."
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
            notes=notes,
        )

    # --- ETFs ----------------------------------------------------------------------

    async def _store_etfs(self, day: date, etfs: list[EtfDay]) -> int:
        """Store one day's ETF rows (stamped like every official daily row) and record it."""
        fetched_at = self._clock()
        stamp = stamp_available_at(publication_moment(day, ETF_DAILY_PUBLISHED), fetched_at)
        rows = [item.model_copy(update={"available_at": stamp}) for item in etfs]
        written = await self.repository.upsert_etf_days(rows)
        await self.repository.record_fetch_coverage(
            [
                FetchCoverage(
                    source=self.source,
                    dataset=ETF_DAY_DATASET,
                    symbol=self.market,
                    period=day.isoformat(),
                    status="ok" if rows else "empty",
                    row_count=len(rows),
                    fetched_at=fetched_at,
                )
            ]
        )
        return written

    async def _backfill_etfs(self, *, end: date, now: datetime) -> tuple[int, int]:
        """``(requests made, rows stored)`` for recent days whose stock rows exist already.

        Bounded (``ETF_BACKFILL_DAYS`` per run, a 3-week window), newest first, resumable
        through the ``etf_day`` coverage, and never during the trading session. Only days
        the stock coverage says traded are asked for, so holidays cost nothing.
        """
        provider = self._etf_provider
        if provider is None or in_session(now):
            return 0, 0
        local_now = now.astimezone(TAIPEI)
        first = max(end - timedelta(days=ETF_BACKFILL_WINDOW_DAYS), date.min)
        days = [
            first + timedelta(days=offset)
            for offset in range((end - first).days + 1)
            if is_trading_day(first + timedelta(days=offset))
            and first + timedelta(days=offset) <= local_now.date()
        ]
        # Today's report is not out before the evening publication window.
        if days and days[-1] == local_now.date() and local_now.time() < time(15, 0):
            days.pop()
        if not days:
            return 0, 0
        periods = [day.isoformat() for day in days]
        coverage = await self.repository.list_fetch_coverage(
            self.source, ETF_DAY_DATASET, self.market, periods
        )
        stock_coverage = await self.repository.list_fetch_coverage(
            self.source, MARKET_STOCK_DATASET, self.market, periods
        )
        requests = rows = 0
        for day in sorted(days, reverse=True):
            if requests >= ETF_BACKFILL_DAYS:
                break
            entry = coverage.get(day.isoformat())
            if entry is not None and (
                entry.status == "ok" or now - entry.fetched_at < EMPTY_RETRY_AFTER
            ):
                continue
            stocks = stock_coverage.get(day.isoformat())
            if stocks is None or stocks.status != "ok":
                continue  # not stored as a trading day (yet): the stock loop owns it
            requests += 1
            rows += await self._store_etfs(day, await provider.fetch_market_etf_day(day))
        if requests:
            logger.info("%s ETF backfill: %s day(s), %s rows", self.market, requests, rows)
        return requests, rows


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
