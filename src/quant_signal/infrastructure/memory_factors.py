"""In-memory twin of ``db/factor_repository.py`` (tests and demo mode)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from typing import Any

from quant_signal.domain.models import MarketFactorSnapshot, MarketStockDay


class MemoryFactorDataMixin:
    market_stock_days: dict[tuple[str, date, str], MarketStockDay]

    def _factors(self) -> dict[tuple[str, date], list[MarketFactorSnapshot]]:
        if not hasattr(self, "market_factors"):
            self.market_factors: dict[tuple[str, date], list[MarketFactorSnapshot]] = {}
        return self.market_factors

    async def market_flow_through(self, end: date) -> dict[str, date]:
        result: dict[str, date] = {}
        for item in self.market_stock_days.values():
            if item.foreign_net is None or item.trading_date > end:
                continue
            if item.market not in result or item.trading_date > result[item.market]:
                result[item.market] = item.trading_date
        return result

    async def iter_market_histories(
        self, end: date, sessions: int
    ) -> AsyncIterator[tuple[str, str, str, list[tuple[Any, ...]]]]:
        days = sorted(
            {i.trading_date for i in self.market_stock_days.values() if i.trading_date <= end}
        )
        if not days:
            return
        oldest = days[-sessions:][0]
        grouped: dict[str, dict[date, MarketStockDay]] = {}
        for item in self.market_stock_days.values():
            if oldest <= item.trading_date <= end:
                grouped.setdefault(item.symbol, {})[item.trading_date] = item
        for symbol in sorted(grouped):
            by_date = grouped[symbol]
            last = by_date[max(by_date)]
            rows = [
                (
                    d,
                    r.close,
                    r.change,
                    r.high,
                    r.low,
                    r.volume,
                    r.turnover_value,
                    r.foreign_net,
                    r.trust_net,
                    r.dealer_net,
                    r.total_net,
                    r.margin_balance,
                    r.margin_change,
                    r.short_balance,
                    r.short_change,
                )
                for d, r in sorted(by_date.items())
            ]
            yield symbol, last.name, last.market, rows

    async def replace_market_factors(
        self, trading_date: date, version: str, rows: list[MarketFactorSnapshot]
    ) -> int:
        self._factors()[(version, trading_date)] = list(rows)
        return len(rows)

    async def prune_market_factors(self, version: str, keep: int) -> int:
        store = self._factors()
        dates = sorted((d for v, d in store if v == version), reverse=True)
        removed = 0
        for day in dates[keep:]:
            removed += len(store.pop((version, day)))
        return removed

    async def latest_market_factor_date(
        self, version: str, as_of: date | None = None
    ) -> date | None:
        dates = [d for v, d in self._factors() if v == version and (as_of is None or d <= as_of)]
        return max(dates, default=None)

    async def list_market_factors(
        self, trading_date: date, version: str
    ) -> list[MarketFactorSnapshot]:
        return list(self._factors().get((version, trading_date), []))
