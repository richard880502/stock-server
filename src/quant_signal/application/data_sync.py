from __future__ import annotations

import logging
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from quant_signal.application.ports import QuantRepository
from quant_signal.domain.models import (
    Bar,
    CorporateAction,
    FetchCoverage,
    InstitutionalFlow,
    MarketObservation,
    SyncJobKind,
)
from quant_signal.infrastructure.providers.tpex import (
    TPEX_SOURCE,
    TpexProvider,
)
from quant_signal.infrastructure.providers.twse import (
    TWSE_SOURCE,
    TwseDailyMarket,
)
from quant_signal.quant.adjustments import apply_forward_adjustments

logger = logging.getLogger(__name__)

TAIPEI = ZoneInfo("Asia/Taipei")
DAILY_BARS_DATASET = "daily_bars"
INDEX_BARS_DATASET = "index_bars"
INSTITUTIONAL_DATASET = "institutional_flow"


class DataSyncPending(Exception):
    """Raised when data is missing and a background sync has been queued.

    Deliberately not a ``ValueError``: callers map ``ValueError`` to a client
    error, while this is a temporary "try again shortly" condition.
    """

    def __init__(self, symbol: str, *, retry_after_seconds: int = 30) -> None:
        self.symbol = symbol
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"資料同步中：{symbol} 的歷史資料已排入背景同步，"
            f"請約 {retry_after_seconds} 秒後重試。"
        )


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _month_end(day: date) -> date:
    first = _month_start(day)
    following = (
        date(first.year + 1, 1, 1)
        if first.month == 12
        else date(first.year, first.month + 1, 1)
    )
    return following - timedelta(days=1)


def _months_between(start: date, end: date) -> list[date]:
    months: list[date] = []
    current = _month_start(start)
    while current <= end:
        months.append(current)
        current = _month_end(current) + timedelta(days=1)
    return months


def _fetched_after(entry: FetchCoverage | None, closed_on: date) -> bool:
    """True when the slice was fetched after ``closed_on`` had fully passed."""
    if entry is None:
        return False
    return entry.fetched_at.astimezone(TAIPEI).date() > closed_on


def _consecutive_runs(days: list[date], *, step: Callable[[date], date]) -> list[list[date]]:
    runs: list[list[date]] = []
    for day in days:
        if runs and step(runs[-1][-1]) == day:
            runs[-1].append(day)
        else:
            runs.append([day])
    return runs


class WritableMarketRepository(Protocol):
    async def list_bars(
        self,
        symbol: str,
        *,
        end: date | None = None,
        start: date | None = None,
    ) -> list[Bar]: ...

    async def list_fetch_coverage(
        self,
        source: str,
        dataset: str,
        symbol: str,
        periods: list[str],
    ) -> dict[str, FetchCoverage]: ...

    async def record_fetch_coverage(self, entries: list[FetchCoverage]) -> None: ...

    async def upsert_bars(
        self,
        bars: list[Bar],
        *,
        instrument_name: str | None = None,
        market: str | None = None,
    ) -> int: ...

    async def upsert_market_observations(
        self,
        observations: list[MarketObservation],
    ) -> int: ...

    async def upsert_institutional_flows(
        self,
        flows: list[InstitutionalFlow],
    ) -> int: ...


class OfficialMarketProvider(Protocol):
    instrument_names: dict[str, str]

    async def fetch_symbol_bars(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
    ) -> list[Bar]: ...

    async def fetch_index_bars(self, *, start: date, end: date) -> list[Bar]: ...

    async def fetch_corporate_actions(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
    ) -> list[CorporateAction]: ...

    async def fetch_market_quotes(
        self,
        trading_date: date,
    ) -> TwseDailyMarket | None: ...

    async def fetch_market_day(
        self,
        trading_date: date,
    ) -> TwseDailyMarket | None: ...

    async def enrich_market_day(
        self,
        market: TwseDailyMarket,
    ) -> TwseDailyMarket: ...

    async def fetch_institutional_flows(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
        concurrency: int = 4,
    ) -> list[InstitutionalFlow]: ...

class DataSyncReport(BaseModel):
    provider: str
    started_at: datetime
    completed_at: datetime
    requested_start: date
    requested_end: date
    bars_received: int = Field(ge=0)
    bars_inserted: int = Field(ge=0)
    corporate_actions_received: int = Field(default=0, ge=0)
    adjusted_bars: int = Field(default=0, ge=0)
    market_observations_received: int = Field(default=0, ge=0)
    market_observations_inserted: int = Field(default=0, ge=0)
    skipped_dates: int = Field(default=0, ge=0)
    incomplete_capital_dates: list[date] = Field(default_factory=list)
    institutional_flows_received: int = Field(default=0, ge=0)
    institutional_flows_inserted: int = Field(default=0, ge=0)
    periods_fetched: int = Field(default=0, ge=0)
    periods_skipped: int = Field(default=0, ge=0)
    notes: list[str] = Field(default_factory=list)


class TwseDataSyncService:
    provider_source = TWSE_SOURCE
    market_code = "TW"
    index_symbol = "^TWII"
    index_name = "臺灣加權股價指數"
    adjustment_start = date(2003, 5, 5)

    def __init__(
        self,
        repository: WritableMarketRepository,
        provider: OfficialMarketProvider,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self._clock = clock or (lambda: datetime.now(UTC))

    async def sync_symbol(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
    ) -> DataSyncReport:
        """Fetch only the months not already stored after they closed.

        Closed months are immutable upstream, so each is requested once and
        recorded in ``fetch_coverage``. The still-open month is always
        refreshed. Adjustment factors resume from the last stored bar, which
        also keeps the market-wide corporate-action request small.
        """
        started_at = self._clock()
        normalized = symbol.strip().upper()
        is_index = normalized == self.index_symbol
        dataset = INDEX_BARS_DATASET if is_index else DAILY_BARS_DATASET
        months = _months_between(start, end)
        coverage = await self.repository.list_fetch_coverage(
            self.provider_source,
            dataset,
            normalized,
            [month.strftime("%Y-%m") for month in months],
        )
        pending = [
            month
            for month in months
            if not _fetched_after(
                coverage.get(month.strftime("%Y-%m")), _month_end(month)
            )
        ]
        trusted: set[date] = set()
        if not is_index:
            # Stored bars with no coverage record predate this ledger (or came from
            # an older sync), so their adjustment factors cannot be trusted as a
            # starting point. Rebuild those months on one consistent basis.
            stored_months = sorted(
                {
                    _month_start(bar.trading_date)
                    for bar in await self.repository.list_bars(normalized, end=end)
                    if bar.source == self.provider_source
                }
            )
            known = await self.repository.list_fetch_coverage(
                self.provider_source,
                dataset,
                normalized,
                [month.strftime("%Y-%m") for month in stored_months],
            )
            trusted = {
                month
                for month in stored_months
                if known.get(month.strftime("%Y-%m")) is not None
            }
            pending = sorted(set(pending) | (set(stored_months) - trusted))
        received = inserted = actions_received = adjusted = 0
        for run in _consecutive_runs(
            pending, step=lambda day: _month_end(day) + timedelta(days=1)
        ):
            run_start = run[0]
            run_end = min(_month_end(run[-1]), end)
            run_bars, run_actions = await self._fetch_run(
                normalized, run_start, run_end, is_index=is_index, trusted=trusted
            )
            name = (
                self.index_name if is_index else self.provider.instrument_names.get(normalized)
            )
            inserted += await self.repository.upsert_bars(
                run_bars,
                instrument_name=name,
                market=self.market_code,
            )
            received += len(run_bars)
            actions_received += len(run_actions)
            adjusted += sum(bar.adjusted_close is not None for bar in run_bars)
            by_month: dict[date, int] = defaultdict(int)
            for bar in run_bars:
                by_month[_month_start(bar.trading_date)] += 1
            await self.repository.record_fetch_coverage(
                [
                    FetchCoverage(
                        source=self.provider_source,
                        dataset=dataset,
                        symbol=normalized,
                        period=month.strftime("%Y-%m"),
                        status="ok" if by_month[month] else "empty",
                        row_count=by_month[month],
                        fetched_at=self._clock(),
                    )
                    for month in run
                    # A past month cut short by ``end`` must not be marked complete.
                    # The open month is safe to record: it is never treated as
                    # covered until a fetch happens after it has closed.
                    if _month_end(month) <= end or self._clock().astimezone(TAIPEI).date()
                    <= _month_end(month)
                ]
            )
        return DataSyncReport(
            provider=self.provider_source,
            started_at=started_at,
            completed_at=self._clock(),
            requested_start=start,
            requested_end=end,
            bars_received=received,
            bars_inserted=inserted,
            corporate_actions_received=actions_received,
            adjusted_bars=adjusted,
            periods_fetched=len(pending),
            periods_skipped=len(set(months) - set(pending)),
            notes=[
                (
                    "Prices use point-in-time-safe forward adjustment from official "
                    "ex-right/ex-dividend reference prices."
                ),
                "Closed months already stored are skipped; only open or missing ones are fetched.",
            ],
        )

    async def _fetch_run(
        self,
        symbol: str,
        run_start: date,
        run_end: date,
        *,
        is_index: bool,
        trusted: set[date] | None = None,
    ) -> tuple[list[Bar], list[CorporateAction]]:
        if is_index:
            return await self.provider.fetch_index_bars(start=run_start, end=run_end), []
        bars = await self.provider.fetch_symbol_bars(symbol, start=run_start, end=run_end)
        if not bars:
            return [], []
        # Only this exchange's own bars may seed the factor: a synthetic demo
        # bar on the previous day carries no corporate-action history.
        prior = [
            bar
            for bar in await self.repository.list_bars(
                symbol, end=run_start - timedelta(days=1)
            )
            if bar.source == self.provider_source
        ]
        resume = (
            prior[-1]
            if prior
            and prior[-1].adjusted_close is not None
            and (trusted is None or _month_start(prior[-1].trading_date) in trusted)
            else None
        )
        # With a stored predecessor only newer events matter; otherwise rebuild
        # the factor from the first event the exchange publishes.
        actions = await self.provider.fetch_corporate_actions(
            symbol,
            start=run_start if resume else self.adjustment_start,
            end=run_end,
        )
        adjusted = apply_forward_adjustments(
            bars,
            actions,
            initial_factor=resume.adjustment_factor if resume else 1.0,
        )
        return adjusted, actions

    async def sync_institutional_flows(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
    ) -> DataSyncReport:
        started_at = self._clock()
        normalized = symbol.strip().upper()
        weekdays = [
            start + timedelta(days=offset)
            for offset in range((end - start).days + 1)
            if (start + timedelta(days=offset)).weekday() < 5
        ]
        coverage = await self.repository.list_fetch_coverage(
            self.provider_source,
            INSTITUTIONAL_DATASET,
            normalized,
            [day.isoformat() for day in weekdays],
        )
        pending = [
            day
            for day in weekdays
            if not _fetched_after(coverage.get(day.isoformat()), day)
        ]
        received = inserted = 0
        for run in _consecutive_runs(
            pending, step=lambda day: day + timedelta(days=3 if day.weekday() == 4 else 1)
        ):
            flows = await self.provider.fetch_institutional_flows(
                normalized, start=run[0], end=run[-1]
            )
            inserted += await self.repository.upsert_institutional_flows(flows)
            received += len(flows)
            present = {flow.trading_date for flow in flows}
            await self.repository.record_fetch_coverage(
                [
                    FetchCoverage(
                        source=self.provider_source,
                        dataset=INSTITUTIONAL_DATASET,
                        symbol=normalized,
                        period=day.isoformat(),
                        status="ok" if day in present else "empty",
                        row_count=int(day in present),
                        fetched_at=self._clock(),
                    )
                    for day in run
                ]
            )
        return DataSyncReport(
            provider=self.provider_source,
            started_at=started_at,
            completed_at=self._clock(),
            requested_start=start,
            requested_end=end,
            bars_received=0,
            bars_inserted=0,
            institutional_flows_received=received,
            institutional_flows_inserted=inserted,
            periods_fetched=len(pending),
            periods_skipped=len(weekdays) - len(pending),
            notes=[
                "Stock-level institutional flows are official after-market data.",
                "All records are available only after the same-session close.",
            ],
        )

    async def sync_market_environment(
        self,
        *,
        start: date,
        end: date,
        warmup_calendar_days: int = 400,
    ) -> DataSyncReport:
        started_at = datetime.now(UTC)
        if start >= end:
            raise ValueError("start must be before end")
        history_start = start - timedelta(days=max(warmup_calendar_days, 90))
        histories: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=252))
        observations: list[MarketObservation] = []
        checkpoint: list[MarketObservation] = []
        inserted = 0
        skipped = 0
        incomplete: list[date] = []
        current = history_start
        prefetched_quotes: dict[date, TwseDailyMarket | None] | None = None
        if isinstance(self.provider, TpexProvider):
            weekdays: list[date] = []
            candidate = history_start
            while candidate <= end:
                if candidate.weekday() < 5:
                    weekdays.append(candidate)
                candidate += timedelta(days=1)
            prefetched_quotes = await self.provider.fetch_market_quotes_range(
                weekdays
            )
        processed_weekdays = 0
        while current <= end:
            if current.weekday() >= 5:
                current += timedelta(days=1)
                continue
            quotes_only = (
                prefetched_quotes.get(current)
                if prefetched_quotes is not None
                else await self.provider.fetch_market_quotes(current)
            )
            processed_weekdays += 1
            if processed_weekdays % 25 == 0:
                logger.info(
                    "%s market sync progress: %s (%s observations ready)",
                    self.provider_source,
                    current,
                    len(observations),
                )
            if quotes_only is None:
                skipped += 1
                current += timedelta(days=1)
                continue
            for quote in quotes_only.quotes:
                histories[quote.symbol].append(quote.close)
            if current < start:
                current += timedelta(days=1)
                continue

            full_day = await self.provider.enrich_market_day(quotes_only)
            if not full_day.capital_data_complete:
                incomplete.append(current)
                current += timedelta(days=1)
                continue
            observation = self._build_market_observation(full_day, histories)
            if observation is not None:
                observations.append(observation)
                checkpoint.append(observation)
            if len(checkpoint) >= 10:
                inserted += await self.repository.upsert_market_observations(checkpoint)
                checkpoint.clear()
            current += timedelta(days=1)

        if checkpoint:
            inserted += await self.repository.upsert_market_observations(checkpoint)
        notes = [
            (
                f"Breadth is derived from {self.provider_source} common-stock closes "
                "with up to 252 sessions of rolling history."
            ),
            (
                "Futures open interest is not yet available from this provider "
                "and is neutralized at 0."
            ),
            (
                f"Market observations with source {self.provider_source} take precedence "
                "over synthetic demo data."
            ),
        ]
        return DataSyncReport(
            provider=self.provider_source,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            requested_start=start,
            requested_end=end,
            bars_received=0,
            bars_inserted=0,
            market_observations_received=len(observations),
            market_observations_inserted=inserted,
            skipped_dates=skipped,
            incomplete_capital_dates=incomplete,
            notes=notes,
        )

    @classmethod
    def _build_market_observation(
        cls,
        market: TwseDailyMarket,
        histories: dict[str, deque[float]],
    ) -> MarketObservation | None:
        eligible = [
            quote
            for quote in market.quotes
            if len(histories.get(quote.symbol, ())) >= 60
        ]
        if not eligible:
            return None
        above_ma20 = above_ma60 = new_high = new_low = 0
        up_volume = down_volume = 0.0
        for quote in eligible:
            closes = list(histories[quote.symbol])
            latest = closes[-1]
            above_ma20 += latest > sum(closes[-20:]) / 20
            above_ma60 += latest > sum(closes[-60:]) / 60
            high_low_window = closes[-252:]
            new_high += latest >= max(high_low_window)
            new_low += latest <= min(high_low_window)
            if quote.direction > 0:
                up_volume += quote.volume
            elif quote.direction < 0:
                down_volume += quote.volume

        advancing = sum(quote.direction > 0 for quote in eligible)
        declining = sum(quote.direction < 0 for quote in eligible)
        unchanged = len(eligible) - advancing - declining
        return MarketObservation(
            market=cls.market_code,
            trading_date=market.trading_date,
            index_close=market.index_close,
            total_issues=len(eligible),
            advancing_issues=advancing,
            declining_issues=declining,
            unchanged_issues=unchanged,
            above_ma20_issues=above_ma20,
            above_ma60_issues=above_ma60,
            new_high_52w_issues=new_high,
            new_low_52w_issues=new_low,
            up_volume=up_volume,
            down_volume=down_volume,
            turnover_value=market.turnover_value,
            foreign_net_flow=market.foreign_net_flow,
            investment_trust_net_flow=market.investment_trust_net_flow,
            dealer_net_flow=market.dealer_net_flow,
            futures_net_open_interest=0,
            margin_balance=market.margin_balance,
            short_balance=market.short_balance,
            available_at=datetime.combine(
                market.trading_date,
                datetime.min.time(),
                tzinfo=UTC,
            )
            + timedelta(hours=10),
            source=cls.provider_source,
        )


class TpexDataSyncService(TwseDataSyncService):
    provider_source = TPEX_SOURCE
    market_code = "TWO"
    index_symbol = "^TWOII"
    index_name = "櫃買指數"
    adjustment_start = date(2008, 1, 2)

    def __init__(
        self,
        repository: WritableMarketRepository,
        provider: TpexProvider,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(repository, provider, clock=clock)


class SyncableRepository(QuantRepository, WritableMarketRepository, Protocol):
    """A repository that can both serve and backfill point-in-time bars."""


def _is_tpex_symbol(symbol: str) -> bool:
    return symbol.endswith(".TWO") or symbol == "^TWOII"


async def _recently_synced(
    repository: SyncableRepository,
    symbol: str,
    *,
    within: timedelta = timedelta(hours=6),
) -> bool:
    now = datetime.now(UTC)
    source = TPEX_SOURCE if _is_tpex_symbol(symbol) else TWSE_SOURCE
    dataset = INDEX_BARS_DATASET if symbol.startswith("^") else DAILY_BARS_DATASET
    period = now.astimezone(TAIPEI).strftime("%Y-%m")
    entry = (
        await repository.list_fetch_coverage(source, dataset, symbol, [period])
    ).get(period)
    return entry is not None and now - entry.fetched_at < within


async def ensure_symbol_bar_coverage(
    repository: SyncableRepository,
    symbol: str,
    *,
    as_of: date,
    minimum_bars: int = 60,
    lookback_days: int = 500,
) -> bool:
    """Queue a background backfill when a symbol has too little history.

    Called from the live REST/MCP analysis entry points (never from
    backtests, which must stay deterministic) right before running the
    signal engine. It never touches the exchange itself: it enqueues one
    deduplicated job for the sync worker and raises ``DataSyncPending`` so
    the caller can tell the user to retry. Returns ``False`` when enough bars
    exist. If a sync already ran recently and the symbol is still short (for
    example a fresh listing), it returns ``True`` so the analysis surfaces its
    own "insufficient history" error instead of waiting forever.
    """
    normalized = symbol.strip().upper()
    existing = await repository.list_bars(normalized, end=as_of)
    if len(existing) >= minimum_bars:
        return False
    if await _recently_synced(repository, normalized):
        return True
    behind = max((date.today() - as_of).days, 0)
    await repository.enqueue_sync_job(
        SyncJobKind.SYMBOL,
        normalized,
        lookback_days=lookback_days + behind,
    )
    raise DataSyncPending(normalized)
