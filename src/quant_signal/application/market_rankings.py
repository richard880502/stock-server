"""Rankings and capital-flow series over the stored whole-market snapshot."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Literal

from quant_signal.application.ports import QuantRepository
from quant_signal.application.sync_jobs import ensure_market_snapshots
from quant_signal.domain.models import (
    MarketDataStatus,
    MarketFlowSeries,
    MarketRanking,
    MarketStockDay,
    RankingEntry,
    RankingMetric,
    SyncJobKind,
)

SHARES_PER_LOT = 1000
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
_SHARE_FIELDS = {
    "foreign_net": "foreign_net",
    "trust_net": "trust_net",
    "dealer_net": "dealer_net",
    "total_net": "total_net",
}
_LOT_FIELDS = {"margin_change": "margin_change", "short_change": "short_change"}
# Reports the exchanges publish after the quotes; a session only counts for these
# metrics once the report exists.
_REQUIRED_REPORT = {
    "foreign_net": "foreign_net",
    "trust_net": "trust_net",
    "dealer_net": "dealer_net",
    "total_net": "total_net",
    "margin_change": "margin_change",
    "short_change": "short_change",
}


def _day_change_pct(row: MarketStockDay) -> float | None:
    previous = row.close - row.change
    return row.change / previous * 100 if previous > 0 else None


class MarketRankingService:
    def __init__(self, repository: QuantRepository, *, backfill_days: int = 260) -> None:
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
        require = _REQUIRED_REPORT.get(metric)
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
        rows = await self.repository.list_market_stock_days(dates, scope, as_of=as_of)
        latest = max(dates)
        by_symbol: dict[str, list[MarketStockDay]] = defaultdict(list)
        for row in rows:
            by_symbol[row.symbol].append(row)

        scored: list[tuple[float, list[MarketStockDay]]] = []
        for history in by_symbol.values():
            history.sort(key=lambda item: item.trading_date)
            # Skip names that stopped trading before the latest session.
            if history[-1].trading_date != latest:
                continue
            value = self._value(metric, history)
            if value is not None:
                scored.append((value, history))
        scored.sort(key=lambda pair: pair[0], reverse=order == "desc")

        entries = [
            RankingEntry(
                rank=position,
                symbol=history[-1].symbol,
                name=history[-1].name,
                market=history[-1].market,
                close=history[-1].close,
                day_change_pct=_day_change_pct(history[-1]),
                value=value,
                volume_lots=history[-1].volume // SHARES_PER_LOT,
                turnover_value=history[-1].turnover_value,
            )
            for position, (value, history) in enumerate(scored[:limit], start=1)
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

    @staticmethod
    def _value(metric: str, history: list[MarketStockDay]) -> float | None:
        if metric in _SHARE_FIELDS:
            values = [getattr(r, _SHARE_FIELDS[metric]) for r in history]
            known = [v for v in values if v is not None]
            return sum(known) / SHARES_PER_LOT if known else None
        if metric in _LOT_FIELDS:
            values = [getattr(r, _LOT_FIELDS[metric]) for r in history]
            known = [v for v in values if v is not None]
            return float(sum(known)) if known else None
        if metric == "volume":
            return sum(r.volume for r in history) / SHARES_PER_LOT
        if metric == "turnover":
            return sum(r.turnover_value for r in history)
        if metric == "change_pct":
            # The oldest day's reference price is its close minus its change, so
            # the result is the move over the whole window including that day.
            base = history[0].close - history[0].change
            return (history[-1].close / base - 1) * 100 if base > 0 else None
        raise ValueError(f"unknown ranking metric: {metric}")

    async def flows(
        self,
        *,
        market: Literal["ALL", "TW", "TWO"] = "ALL",
        days: int = 60,
        as_of: date | None = None,
    ) -> MarketFlowSeries:
        scope = None if market == "ALL" else market
        dates = await self.repository.list_market_trading_dates(
            scope, as_of=as_of, limit=days
        )
        if not dates:
            return MarketFlowSeries(
                market=market,
                as_of=as_of,
                data_through=None,
                backfill_pending=await self._request_backfill(),
                days=[],
            )
        totals = await self.repository.market_flow_totals(dates, scope, as_of=as_of)
        return MarketFlowSeries(
            market=market,
            as_of=as_of,
            data_through=max(dates),
            backfill_pending=await self.repository.has_active_sync_job(SyncJobKind.MARKET),
            days=totals,
        )

    async def status(self) -> list[MarketDataStatus]:
        return await self.repository.market_data_status()

    async def _request_backfill(self) -> bool:
        """First visit with an empty table: start the backfill, report it as pending."""
        await ensure_market_snapshots(self.repository, lookback_days=self.backfill_days)
        return await self.repository.has_active_sync_job(SyncJobKind.MARKET)
