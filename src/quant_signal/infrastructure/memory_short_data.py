"""In-memory twin of ``db/short_data_repository.py`` (tests and demo mode)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from quant_signal.domain.short_data import (
    DayTradeDay,
    DividendEvent,
    InsiderHolding,
    ShortLendingDay,
)
from quant_signal.infrastructure.short_data_reads import ShortDataReadsMixin, plan_revisions


def _newest(rows: list[Any], key: Any, cutoff: datetime) -> list[Any]:
    best: dict[tuple, Any] = {}
    for row in rows:
        if row.available_at > cutoff:
            continue
        k = key(row)
        if k not in best or row.revision > best[k].revision:
            best[k] = row
    return list(best.values())


class MemoryShortDataMixin(ShortDataReadsMixin):
    def _sd(self) -> None:
        if not hasattr(self, "sd_store"):
            self.sd_store: dict[str, list[Any]] = {
                "sbl": [],
                "daytrade": [],
                "insider": [],
                "dividend": [],
            }

    def _put(self, name: str, rows: list[Any], key: Any) -> int:
        self._sd()
        store = self.sd_store[name]
        existing: dict[tuple, Any] = {}
        for row in store:
            k = key(row)
            if k not in existing or row.revision > existing[k].revision:
                existing[k] = row
        fresh = plan_revisions(rows, existing, key)
        store.extend(fresh)
        return len(fresh)

    async def upsert_short_lending(self, rows: list[ShortLendingDay]) -> int:
        return self._put("sbl", rows, lambda r: (r.symbol, r.trading_date))

    async def upsert_day_trade(self, rows: list[DayTradeDay]) -> int:
        return self._put("daytrade", rows, lambda r: (r.symbol, r.trading_date))

    async def upsert_insider_holdings(self, rows: list[InsiderHolding]) -> int:
        return self._put("insider", rows, lambda r: (r.symbol, r.period))

    async def upsert_dividend_events(self, rows: list[DividendEvent]) -> int:
        return self._put("dividend", rows, lambda r: (r.symbol, r.ex_date, r.kind))

    async def sd_sbl_rows(
        self, since: date, cutoff: datetime, symbol: str | None = None
    ) -> list[ShortLendingDay]:
        self._sd()
        rows = [
            r
            for r in self.sd_store["sbl"]
            if r.trading_date >= since and (symbol is None or r.symbol == symbol)
        ]
        return _newest(rows, lambda r: (r.symbol, r.trading_date), cutoff)

    async def sd_day_trade_rows(
        self, since: date, cutoff: datetime, symbol: str | None = None
    ) -> list[DayTradeDay]:
        self._sd()
        rows = [
            r
            for r in self.sd_store["daytrade"]
            if r.trading_date >= since and (symbol is None or r.symbol == symbol)
        ]
        return _newest(rows, lambda r: (r.symbol, r.trading_date), cutoff)

    async def sd_latest_date(self, kind: str, cutoff: datetime) -> date | None:
        self._sd()
        rows = self.sd_store["sbl" if kind == "sbl" else "daytrade"]
        days = [r.trading_date for r in rows if r.available_at <= cutoff]
        return max(days) if days else None

    async def sd_volume_rows(
        self, since: date, cutoff: datetime, symbol: str | None = None
    ) -> list[tuple[str, date, int]]:
        return [
            (row.symbol, row.trading_date, row.volume)
            for row in self.market_stock_days.values()  # type: ignore[attr-defined]
            if since <= row.trading_date <= cutoff.date()
            and row.available_at <= cutoff
            and (symbol is None or row.symbol == symbol)
        ]

    async def sd_insider_rows(
        self, cutoff: datetime, symbol: str | None = None
    ) -> list[InsiderHolding]:
        self._sd()
        pool = [r for r in self.sd_store["insider"] if symbol is None or r.symbol == symbol]
        rows = _newest(pool, lambda r: (r.symbol, r.period), cutoff)
        newest: dict[str, InsiderHolding] = {}
        for row in rows:
            if row.symbol not in newest or row.period > newest[row.symbol].period:
                newest[row.symbol] = row
        return list(newest.values())

    async def sd_dividend_rows(
        self, start: date, end: date, cutoff: datetime
    ) -> list[DividendEvent]:
        self._sd()
        rows = [r for r in self.sd_store["dividend"] if start <= r.ex_date <= end]
        return _newest(rows, lambda r: (r.symbol, r.ex_date, r.kind), cutoff)
