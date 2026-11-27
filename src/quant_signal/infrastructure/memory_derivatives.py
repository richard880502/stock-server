"""In-memory twin of ``db/derivatives_repository.py`` (tests and demo mode)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from quant_signal.domain.derivatives import (
    DerivativesRows,
    FuturesInstitutionalDay,
    FuturesLargeTradersDay,
    OptionsInstitutionalDay,
    OptionsOiLevelDay,
    OptionsPcRatioDay,
    StockFuturesDay,
    VixDay,
)
from quant_signal.infrastructure.column_limits import enforce_column_limits
from quant_signal.infrastructure.db import derivatives_models as orm
from quant_signal.infrastructure.derivatives_queries import DerivativesQueryMixin

_ORM = {
    "fut": orm.FuturesInstitutionalRow,
    "pc": orm.OptionsPcRatioRow,
    "opt": orm.OptionsInstitutionalRow,
    "large": orm.FuturesLargeTradersRow,
    "ssf": orm.StockFuturesRow,
    "levels": orm.OptionsOiLevelRow,
    "vix": orm.VixRow,
}


class MemoryDerivativesMixin(DerivativesQueryMixin):
    def _drv_store(self, name: str) -> dict[tuple, Any]:
        if not hasattr(self, "_derivatives"):
            self._derivatives: dict[str, dict[tuple, Any]] = {}
        return self._derivatives.setdefault(name, {})

    def _drv_put(self, name: str, rows: list[Any], key: Any) -> int:
        store = self._drv_store(name)
        written = 0
        for row in rows:
            if key(row) not in store:  # published rows are final
                enforce_column_limits(_ORM[name], row.model_dump())
                store[key(row)] = row
                written += 1
        return written

    async def upsert_futures_institutional(self, rows: list[FuturesInstitutionalDay]) -> int:
        return self._drv_put("fut", rows, lambda r: (r.trading_date, r.contract, r.investor))

    async def upsert_options_pc_ratio(self, rows: list[OptionsPcRatioDay]) -> int:
        return self._drv_put("pc", rows, lambda r: (r.trading_date,))

    async def upsert_options_institutional(self, rows: list[OptionsInstitutionalDay]) -> int:
        return self._drv_put(
            "opt", rows, lambda r: (r.trading_date, r.contract, r.call_put, r.investor)
        )

    async def upsert_futures_large_traders(self, rows: list[FuturesLargeTradersDay]) -> int:
        return self._drv_put("large", rows, lambda r: (r.trading_date, r.contract, r.trader_type))

    async def upsert_stock_futures(self, rows: list[StockFuturesDay]) -> int:
        return self._drv_put("ssf", rows, lambda r: (r.symbol, r.trading_date))

    async def upsert_options_oi_levels(self, rows: list[OptionsOiLevelDay]) -> int:
        return self._drv_put("levels", rows, lambda r: (r.trading_date, r.expiry))

    async def upsert_vix_daily(self, rows: list[VixDay]) -> int:
        return self._drv_put("vix", rows, lambda r: (r.trading_date,))

    def _drv_newest(self, name: str, as_of: datetime, days: int) -> list[Any]:
        visible = [row for row in self._drv_store(name).values() if row.available_at <= as_of]
        keep = set(sorted({row.trading_date for row in visible})[-days:])
        return sorted(
            (row for row in visible if row.trading_date in keep), key=lambda r: r.trading_date
        )

    async def derivatives_rows(self, as_of: datetime, days: int) -> DerivativesRows:
        return DerivativesRows(
            futures=self._drv_newest("fut", as_of, days),
            pc_ratio=self._drv_newest("pc", as_of, days),
            options=self._drv_newest("opt", as_of, days),
            large_traders=self._drv_newest("large", as_of, days),
            vix=self._drv_newest("vix", as_of, days),
            oi_levels=self._drv_newest("levels", as_of, days),
        )

    async def futures_institutional_range(
        self, start: date, end: date
    ) -> list[FuturesInstitutionalDay]:
        return sorted(
            (r for r in self._drv_store("fut").values() if start <= r.trading_date <= end),
            key=lambda r: r.trading_date,
        )

    async def stock_futures_history(
        self, symbol: str, as_of: datetime, days: int
    ) -> list[StockFuturesDay]:
        rows = [
            r
            for r in self._drv_store("ssf").values()
            if r.symbol == symbol.upper() and r.available_at <= as_of
        ]
        return sorted(rows, key=lambda r: r.trading_date)[-days:]

    async def has_stock_futures(self, symbol: str) -> bool:
        return any(r.symbol == symbol.upper() for r in self._drv_store("ssf").values())

    async def latest_stock_futures_days(
        self, as_of: datetime, sessions: int = 6
    ) -> dict[str, list[StockFuturesDay]]:
        out: dict[str, list[StockFuturesDay]] = {}
        for row in self._drv_newest("ssf", as_of, sessions):
            out.setdefault(row.symbol, []).append(row)
        return out

    async def stock_spot_closes(self, symbol: str, start: date, end: date) -> dict[date, float]:
        return {
            r.trading_date: r.close
            for r in self.market_stock_days.values()  # type: ignore[attr-defined]
            if r.symbol == symbol.upper() and start <= r.trading_date <= end
        }
