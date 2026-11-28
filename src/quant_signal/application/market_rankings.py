"""Rankings and capital-flow series over the stored whole-market snapshot."""

from __future__ import annotations

from datetime import date
from typing import Literal

from quant_signal.application.ports import QuantRepository
from quant_signal.application.result_cache import ResultCache
from quant_signal.application.sync_jobs import ensure_market_snapshots
from quant_signal.domain.models import (
    MarketDataStatus,
    MarketFlowSeries,
    MarketRanking,
    RankingEntry,
    RankingMetric,
    SyncJobKind,
)
from quant_signal.domain.ranking import REQUIRED_REPORT, SHARES_PER_LOT

METRIC_UNITS: dict[str, str] = {
    "foreign_net": "張",
    "trust_net": "張",
    "dealer_net": "張",
    "total_net": "張",
    "volume": "張",
    "turnover": "元",
    "change_pct": "%",
    "margin_change": "張",
    "short_change": "張",
}
_status_cache = ResultCache(max_entries=4)
_flows_cache = ResultCache(max_entries=32)


def _day_change_pct(close: float, change: float) -> float | None:
    previous = close - change
    return change / previous * 100 if previous > 0 else None


class MarketRankingService:
    def __init__(self, repository: QuantRepository, *, backfill_days: int = 520) -> None:
        self.repository = repository
        self.backfill_days = backfill_days

    async def ranking(
        self,
        metric: RankingMetric,
        *,
        market: Literal["ALL", "TW", "TWO"] = "ALL",
        window: int = 1,
        order: Literal["desc", "asc"] = "desc",
        limit: int = 20,
        as_of: date | None = None,
    ) -> MarketRanking:
        scope = None if market == "ALL" else market
        require = REQUIRED_REPORT.get(metric)
        dates = await self.repository.list_market_trading_dates(
            scope, as_of=as_of, limit=window, require=require
        )
        awaiting: date | None = None
        if dates and require is not None:
            newest = await self.repository.list_market_trading_dates(
                scope, as_of=as_of, limit=1
            )
            awaiting = newest[0] if newest and newest[0] > max(dates) else None
        if not dates:
            return MarketRanking(
                metric=metric,
                market=market,
                window=window,
                order=order,
                as_of=as_of,
                data_through=None,
                window_start=None,
                trading_days=0,
                unit=METRIC_UNITS[metric],
                backfill_pending=await self._request_backfill(),
                entries=[],
            )
        latest = max(dates)
        ranked = await self.repository.market_ranking(
            metric, dates, scope, as_of=as_of, order=order, limit=limit
        )
        entries = [
            RankingEntry(
                rank=position,
                symbol=item.symbol,
                name=item.name,
                market=item.market,
                close=item.close,
                day_change_pct=_day_change_pct(item.close, item.change),
                value=item.value,
                volume_lots=item.volume // SHARES_PER_LOT,
                turnover_value=item.turnover_value,
            )
            for position, item in enumerate(ranked, start=1)
        ]
        return MarketRanking(
            metric=metric,
            market=market,
            window=window,
            order=order,
            as_of=as_of,
            data_through=latest,
            window_start=min(dates),
            trading_days=len(dates),
            unit=METRIC_UNITS[metric],
            awaiting_report_date=awaiting,
            backfill_pending=await self.repository.has_active_sync_job(SyncJobKind.MARKET),
            entries=entries,
        )

    async def flows(
        self,
        *,
        market: Literal["ALL", "TW", "TWO"] = "ALL",
        days: int = 60,
        as_of: date | None = None,
    ) -> MarketFlowSeries:
        scope = None if market == "ALL" else market
        key = await self._versioned_key("flows", market, days, as_of)

        async def compute() -> MarketFlowSeries:
            dates = await self.repository.list_market_trading_dates(
                scope, as_of=as_of, limit=days
            )
            totals = await self.repository.market_flow_totals(dates, scope, as_of=as_of)
            return MarketFlowSeries(
                market=market,
                as_of=as_of,
                data_through=max(dates) if dates else None,
                backfill_pending=False,
                days=totals,
            )

        series: MarketFlowSeries = await _flows_cache.get_or_compute(key, compute)
        pending = (
            await self._request_backfill()
            if not series.days
            else await self.repository.has_active_sync_job(SyncJobKind.MARKET)
        )
        return series.model_copy(update={"backfill_pending": pending})

    async def status(self) -> list[MarketDataStatus]:
        key = await self._versioned_key("status")
        return list(
            await _status_cache.get_or_compute(key, self.repository.market_data_status)
        )

    async def _versioned_key(self, *parts: object) -> tuple[object, ...]:
        """Cache key: which store, a fingerprint of its rows (a sync changes it), the query."""
        return (
            self.repository.cache_namespace,
            await self.repository.market_data_version(),
            *parts,
        )

    async def _request_backfill(self) -> bool:
        """First visit with an empty table: start the backfill, report it as pending."""
        await ensure_market_snapshots(self.repository, lookback_days=self.backfill_days)
        return await self.repository.has_active_sync_job(SyncJobKind.MARKET)
