from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, Field

from quant_signal.domain.models import (
    Bar,
    CorporateAction,
    InstitutionalFlow,
    MarketObservation,
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


class WritableMarketRepository(Protocol):
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
    ) -> None:
        self.repository = repository
        self.provider = provider

    async def sync_symbol(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
    ) -> DataSyncReport:
        started_at = datetime.now(UTC)
        normalized = symbol.strip().upper()
        actions: list[CorporateAction] = []
        if normalized == self.index_symbol:
            bars = await self.provider.fetch_index_bars(start=start, end=end)
            name = self.index_name
        else:
            bars = await self.provider.fetch_symbol_bars(
                normalized,
                start=start,
                end=end,
            )
            actions = await self.provider.fetch_corporate_actions(
                normalized,
                start=self.adjustment_start,
                end=end,
            )
            bars = apply_forward_adjustments(bars, actions)
            name = self.provider.instrument_names.get(normalized)
        inserted = await self.repository.upsert_bars(
            bars,
            instrument_name=name,
            market=self.market_code,
        )
        return DataSyncReport(
            provider=self.provider_source,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            requested_start=start,
            requested_end=end,
            bars_received=len(bars),
            bars_inserted=inserted,
            corporate_actions_received=len(actions),
            adjusted_bars=sum(bar.adjusted_close is not None for bar in bars),
            notes=[
                (
                    "Prices use point-in-time-safe forward adjustment from official "
                    "ex-right/ex-dividend reference prices."
                )
            ],
        )

    async def sync_institutional_flows(
        self,
        symbol: str,
        *,
        start: date,
        end: date,
    ) -> DataSyncReport:
        started_at = datetime.now(UTC)
        normalized = symbol.strip().upper()
        flows = await self.provider.fetch_institutional_flows(
            normalized,
            start=start,
            end=end,
        )
        inserted = await self.repository.upsert_institutional_flows(flows)
        return DataSyncReport(
            provider=self.provider_source,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            requested_start=start,
            requested_end=end,
            bars_received=0,
            bars_inserted=0,
            institutional_flows_received=len(flows),
            institutional_flows_inserted=inserted,
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
    ) -> None:
        super().__init__(repository, provider)
