"""Read side of the official events feed (events, risk flags, revenue metrics)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from quant_signal.application.data_sync import TAIPEI
from quant_signal.application.ports import QuantRepository
from quant_signal.application.stocks import normalize_listed_symbol
from quant_signal.domain.models import (
    ClassifiedEvent,
    HolderPoint,
    MarketEvent,
    MarketRiskFlags,
    StockEvents,
    StockHolders,
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


HOLDERS_NOTE = (
    "集保戶股權分散表（每週五基準，週末公布）自本系統啟用日起累積；"
    "≥400 張/≥1,000 張為持股超過 40 萬/100 萬股之人數與占集保庫存比例，散戶為 10 張以下。"
    "外資持股與發行股數為最新一個交易日。"
)


class StockHoldersService:
    def __init__(self, repository: QuantRepository) -> None:
        self.repository = repository

    async def get(self, raw_symbol: str, *, as_of: date, weeks: int = 26) -> StockHolders:
        symbol = await normalize_listed_symbol(self.repository, raw_symbol)
        cutoff = as_of_cutoff(as_of)
        history = await self.repository.shareholding_history(symbol, cutoff, weeks)
        foreign = await self.repository.latest_foreign_holding(symbol, cutoff)
        return StockHolders(
            symbol=symbol,
            as_of=as_of,
            series=[
                HolderPoint.model_validate(
                    {name: getattr(row, name) for name in HolderPoint.model_fields}
                )
                for row in history
            ],
            shares_outstanding=foreign.shares_outstanding if foreign else None,
            foreign_shares=foreign.foreign_shares if foreign else None,
            foreign_holding_pct=foreign.foreign_holding_pct if foreign else None,
            foreign_data_date=foreign.trading_date if foreign else None,
            note=HOLDERS_NOTE,
        )
