"""Backend-independent read methods composed from the repository's raw derivatives reads.

Both ``db/derivatives_repository.py`` and ``memory_derivatives.py`` provide the primitives
(``derivatives_rows``, ``latest_stock_futures_days``, ``futures_institutional_range``,
``stock_spot_closes``); the lean reads the integrator plugs into the screener and the AI
context live here once.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from quant_signal.domain.derivatives import (
    DerivativesRows,
    FuturesInstitutionalDay,
    MarketDerivativesLatest,
    StockFuturesDay,
    StockFuturesMetrics,
)
from quant_signal.quant.derivatives import (
    latest_market_summary,
    stock_futures_metrics,
    tx_equivalent_net_oi,
)

SUMMARY_DAYS = 60


class DerivativesQueryMixin:
    async def derivatives_rows(self, as_of: datetime, days: int) -> DerivativesRows:
        raise NotImplementedError

    async def latest_stock_futures_days(
        self, as_of: datetime, sessions: int = 6
    ) -> dict[str, list[StockFuturesDay]]:
        raise NotImplementedError

    async def futures_institutional_range(
        self, start: date, end: date
    ) -> list[FuturesInstitutionalDay]:
        raise NotImplementedError

    async def list_market_closes(
        self, dates: list[date], market: str
    ) -> list[tuple[str, date, float, float, int]]:
        raise NotImplementedError

    async def latest_market_derivatives(self, as_of: datetime) -> MarketDerivativesLatest | None:
        """Newest market-level derivatives summary visible at ``as_of`` (None if no data)."""
        rows = await self.derivatives_rows(as_of, SUMMARY_DAYS + 1)
        return latest_market_summary(rows, days=SUMMARY_DAYS)

    async def latest_stock_futures_metrics(self, as_of: datetime) -> dict[str, StockFuturesMetrics]:
        """Per underlying symbol: OI, OI change 1d/5d (percent), near-month basis (percent)."""
        histories = await self.latest_stock_futures_days(as_of, 6)
        if not histories:
            return {}
        newest = {history[-1].trading_date for history in histories.values()}
        spot: dict[tuple[str, date], float] = {}
        for market in ("TW", "TWO"):
            for symbol, day, close, _, _ in await self.list_market_closes(sorted(newest), market):
                spot[(symbol, day)] = close
        out: dict[str, StockFuturesMetrics] = {}
        for symbol, history in sorted(histories.items()):
            metrics = stock_futures_metrics(history, spot.get((symbol, history[-1].trading_date)))
            if metrics is not None:
                out[symbol] = metrics
        return out

    async def foreign_tx_equiv_net_oi_series(self, start: date, end: date) -> dict[date, float]:
        """Foreign investors' TX-equivalent net open interest (TX + MTX/4 + TMF/20) by date."""
        rows = await self.futures_institutional_range(start, end)
        return tx_equivalent_net_oi(rows, "foreign")

    async def foreign_tx_equiv_net_oi_on(self, day: date) -> float | None:
        series: dict[date, float] = await self.foreign_tx_equiv_net_oi_series(day, day)
        return series.get(day)


def _convert(model: Any, row: Any) -> Any:
    return model.model_validate({name: getattr(row, name) for name in model.model_fields})
