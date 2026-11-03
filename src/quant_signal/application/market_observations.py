"""Derives the daily market-environment observation from the stored whole-market data.

Breadth (advancers, MA participation, 52-week highs, up/down volume) is computed
from ``market_stock_daily``; only the few official market-level figures that
cannot be derived from stocks (index close, total turnover, institutional NT$
flows, margin and short balances) are requested, one ``fetch_market_day`` per new
trading day. This replaces re-downloading every day of a 400-day warm-up.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, Protocol

from quant_signal.application.data_sync import DataSyncReport, _fetched_after
from quant_signal.domain.models import FetchCoverage, MarketObservation

logger = logging.getLogger(__name__)

OBSERVATION_DATASET = "market_observation"


class CapitalDay(Protocol):
    trading_date: date
    index_close: float
    turnover_value: float
    foreign_net_flow: float
    investment_trust_net_flow: float
    dealer_net_flow: float
    margin_balance: float
    short_balance: float
    capital_data_complete: bool


class ObservationProvider(Protocol):
    async def fetch_market_day(self, trading_date: date) -> CapitalDay | None: ...


class ObservationRepository(Protocol):
    async def list_market_trading_dates(
        self, market: str | None, *, as_of: date | None, limit: int, require: str | None = None
    ) -> list[date]: ...

    async def list_market_closes(
        self, dates: list[date], market: str
    ) -> list[tuple[str, date, float, float, int]]: ...

    async def list_market_observations(
        self, market: str, *, end: date | None = None, start: date | None = None
    ) -> list[MarketObservation]: ...

    async def upsert_market_observations(self, observations: list[MarketObservation]) -> int: ...

    async def list_fetch_coverage(
        self, source: str, dataset: str, symbol: str, periods: list[str]
    ) -> dict[str, FetchCoverage]: ...

    async def record_fetch_coverage(self, entries: list[FetchCoverage]) -> None: ...


def compute_breadth(
    today: list[tuple[str, float, float, int]],
    histories: dict[str, deque[float]],
    *,
    minimum_history: int,
) -> dict[str, float] | None:
    """Breadth over stocks with enough history, from today's (symbol, close, change, volume)."""
    eligible = [row for row in today if len(histories[row[0]]) >= minimum_history]
    if not eligible:
        return None
    above20 = above60 = new_high = new_low = 0
    up_volume = down_volume = 0.0
    advancing = declining = 0
    for symbol, close, change, volume in eligible:
        closes = histories[symbol]
        window = list(closes)
        above20 += close > sum(window[-20:]) / min(20, len(window))
        above60 += close > sum(window[-60:]) / min(60, len(window))
        new_high += close >= max(window)
        new_low += close <= min(window)
        if change > 0:
            advancing += 1
            up_volume += volume
        elif change < 0:
            declining += 1
            down_volume += volume
    return {
        "total": len(eligible),
        "advancing": advancing,
        "declining": declining,
        "above20": above20,
        "above60": above60,
        "new_high": new_high,
        "new_low": new_low,
        "up_volume": up_volume,
        "down_volume": down_volume,
    }


class MarketObservationSyncService:
    """Builds missing observations, oldest first, and records what it handled."""

    def __init__(
        self,
        repository: ObservationRepository,
        provider: ObservationProvider,
        *,
        market: Literal["TW", "TWO"],
        source: str,
        history_sessions: int = 252,
        minimum_history: int = 252,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.market = market
        self.source = source
        self.history_sessions = history_sessions
        self.minimum_history = minimum_history
        self._clock = clock or (lambda: datetime.now(UTC))

    async def sync(self, *, end: date, max_days: int = 150) -> DataSyncReport:
        started_at = self._clock()
        dates_desc = await self.repository.list_market_trading_dates(
            self.market, as_of=end, limit=self.history_sessions + max_days
        )
        all_dates = sorted(dates_desc)
        have = {
            item.trading_date
            for item in await self.repository.list_market_observations(self.market, end=end)
            if item.source == self.source
        }
        coverage = await self.repository.list_fetch_coverage(
            self.source,
            OBSERVATION_DATASET,
            self.market,
            [day.isoformat() for day in all_dates],
        )
        # A session needs `minimum_history` sessions of closes behind it (itself included).
        feasible = all_dates[self.minimum_history - 1 :]
        targets = [
            day
            for day in feasible
            if day not in have and not _fetched_after(coverage.get(day.isoformat()), day)
        ][-max_days:]
        built: list[MarketObservation] = []
        incomplete: list[date] = []
        if targets:
            closes = await self.repository.list_market_closes(all_dates, self.market)
            by_date: dict[date, list[tuple[str, float, float, int]]] = defaultdict(list)
            for symbol, day, close, change, volume in closes:
                by_date[day].append((symbol, close, change, volume))
            histories: dict[str, deque[float]] = defaultdict(
                lambda: deque(maxlen=self.history_sessions)
            )
            target_set = set(targets)
            for day in all_dates:
                rows = by_date.get(day, [])
                for symbol, close, _, _ in rows:
                    histories[symbol].append(close)
                if day not in target_set:
                    continue
                observation = await self._observe(day, rows, histories)
                if observation is None:
                    incomplete.append(day)
                else:
                    built.append(observation)
        inserted = await self.repository.upsert_market_observations(built) if built else 0
        built_days = {item.trading_date for item in built}
        await self.repository.record_fetch_coverage(
            [
                FetchCoverage(
                    source=self.source,
                    dataset=OBSERVATION_DATASET,
                    symbol=self.market,
                    period=day.isoformat(),
                    status="ok" if day in built_days else "empty",
                    row_count=int(day in built_days),
                    fetched_at=self._clock(),
                )
                for day in targets
            ]
        )
        notes = [
            "Breadth is derived from stored whole-market closes; capital figures are official."
        ]
        if not feasible:
            notes.append(
                f"Waiting for {self.minimum_history} stored sessions "
                f"(have {len(all_dates)}); no observation built yet."
            )
        return DataSyncReport(
            provider=self.source,
            started_at=started_at,
            completed_at=self._clock(),
            requested_start=targets[0] if targets else end,
            requested_end=end,
            bars_received=0,
            bars_inserted=0,
            market_observations_received=len(built),
            market_observations_inserted=inserted,
            incomplete_capital_dates=incomplete,
            periods_fetched=len(targets),
            periods_skipped=len(feasible) - len(targets),
            notes=notes,
        )

    async def _observe(
        self,
        day: date,
        rows: list[tuple[str, float, float, int]],
        histories: dict[str, deque[float]],
    ) -> MarketObservation | None:
        breadth = compute_breadth(rows, histories, minimum_history=self.minimum_history)
        if breadth is None:
            return None
        market = await self.provider.fetch_market_day(day)
        if market is None or not market.capital_data_complete:
            return None
        total = int(breadth["total"])
        advancing, declining = int(breadth["advancing"]), int(breadth["declining"])
        return MarketObservation(
            market=self.market,
            trading_date=day,
            index_close=market.index_close,
            total_issues=total,
            advancing_issues=advancing,
            declining_issues=declining,
            unchanged_issues=total - advancing - declining,
            above_ma20_issues=int(breadth["above20"]),
            above_ma60_issues=int(breadth["above60"]),
            new_high_52w_issues=int(breadth["new_high"]),
            new_low_52w_issues=int(breadth["new_low"]),
            up_volume=breadth["up_volume"],
            down_volume=breadth["down_volume"],
            turnover_value=market.turnover_value,
            foreign_net_flow=market.foreign_net_flow,
            investment_trust_net_flow=market.investment_trust_net_flow,
            dealer_net_flow=market.dealer_net_flow,
            futures_net_open_interest=0,
            margin_balance=market.margin_balance,
            short_balance=market.short_balance,
            available_at=datetime.combine(day, time(0), tzinfo=UTC) + timedelta(hours=10),
            source=self.source,
        )
