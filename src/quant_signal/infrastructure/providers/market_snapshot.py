"""Merges the separate whole-market official reports into one row per stock."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from quant_signal.domain.models import MarketStockDay


@dataclass(frozen=True)
class StockFlow:
    foreign_net: int
    trust_net: int
    dealer_net: int
    total_net: int


@dataclass(frozen=True)
class StockCredit:
    margin_balance: int
    margin_change: int
    short_balance: int
    short_change: int


def to_int(value: Any) -> int:
    text = str(value if value is not None else "").strip().replace(",", "")
    if text in {"", "--", "---"}:
        raise ValueError(f"not an integer report value: {value!r}")
    return int(float(text))


def build_market_stock_days(
    quotes: Iterable[Any],
    *,
    market: Literal["TW", "TWO"],
    source: str,
    available_at: datetime,
    flows: Mapping[str, StockFlow],
    credit: Mapping[str, StockCredit],
    trading_date: date,
) -> list[MarketStockDay]:
    suffix = ".TW" if market == "TW" else ".TWO"
    # A published institutional report only lists stocks that institutions
    # traded, so absence there means a net of zero; an empty report means it was
    # not published yet and the fields stay unknown.
    flows_published = bool(flows)
    zero_flow = StockFlow(0, 0, 0, 0)
    rows: list[MarketStockDay] = []
    for quote in quotes:
        code = quote.symbol.removesuffix(suffix)
        flow = flows.get(code, zero_flow) if flows_published else None
        stock_credit = credit.get(code)
        rows.append(
            MarketStockDay(
                symbol=quote.symbol,
                name=quote.name,
                market=market,
                trading_date=trading_date,
                open=quote.open,
                high=quote.high,
                low=quote.low,
                close=quote.close,
                volume=quote.volume,
                turnover_value=quote.turnover_value,
                change=quote.change,
                foreign_net=flow.foreign_net if flow else None,
                trust_net=flow.trust_net if flow else None,
                dealer_net=flow.dealer_net if flow else None,
                total_net=flow.total_net if flow else None,
                margin_balance=stock_credit.margin_balance if stock_credit else None,
                margin_change=stock_credit.margin_change if stock_credit else None,
                short_balance=stock_credit.short_balance if stock_credit else None,
                short_change=stock_credit.short_change if stock_credit else None,
                available_at=available_at,
                source=source,
            )
        )
    return rows
