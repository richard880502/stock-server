"""Read side of the official events feed (events, risk flags, revenue metrics)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from quant_signal.application.data_sync import TAIPEI
from quant_signal.application.ports import QuantRepository
from quant_signal.application.stocks import normalize_listed_symbol
from quant_signal.domain.models import (
    ClassifiedEvent,
    MarketEvent,
    MarketRiskFlags,
    StockEvents,
)
from quant_signal.quant.event_classifier import classify_material
from quant_signal.quant.revenue import revenue_metrics

NOTE = "官方 OpenAPI 只提供最新一期；本系統自啟用日起累積歷史，as_of 之前尚未抓取的資料不會出現。"


def as_of_cutoff(as_of: date) -> datetime:
    """Everything fetched through the end of ``as_of`` (Taipei time)."""
    return datetime.combine(as_of, time.max, TAIPEI)


def classify_event(event: MarketEvent) -> ClassifiedEvent:
    if event.kind == "material":
        category, polarity = classify_material(event.title, event.clause)
    else:
        category, polarity = "dividend", "neutral"
    return ClassifiedEvent(
        symbol=event.symbol,
        kind=event.kind,
        announced_at=event.announced_at,
        title=event.title,
        clause=event.clause,
        detail=event.detail,
        effective_date=event.effective_date,
        source=event.source,
        category=category,
        polarity=polarity,
        available_at=event.available_at,
    )


class StockEventsService:
    def __init__(self, repository: QuantRepository) -> None:
        self.repository = repository

    async def get(self, raw_symbol: str, *, as_of: date, days: int = 30) -> StockEvents:
        symbol = await normalize_listed_symbol(self.repository, raw_symbol)
        cutoff = as_of_cutoff(as_of)
        since = datetime.combine(as_of - timedelta(days=days), time.min, TAIPEI)
        events = await self.repository.list_market_events([symbol], since, cutoff)
        flags = await self.repository.active_risk_flags(cutoff, [symbol])
        history = await self.repository.monthly_revenue(symbol, cutoff, 13)
        return StockEvents(
            symbol=symbol,
            as_of=as_of,
            days=days,
            events=[classify_event(event) for event in events],
            risk_flags=flags,
            revenue=revenue_metrics(history),
            note=NOTE,
        )


class MarketRiskFlagService:
    def __init__(self, repository: QuantRepository) -> None:
        self.repository = repository

    async def get(self, *, as_of: date) -> MarketRiskFlags:
        flags = await self.repository.active_risk_flags(as_of_cutoff(as_of))
        return MarketRiskFlags(as_of=as_of, count=len(flags), flags=flags)
