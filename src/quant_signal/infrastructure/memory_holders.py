"""In-memory twin of ``db/holders_repository.py`` (tests and demo mode)."""

from __future__ import annotations

from datetime import datetime

from quant_signal.domain.models import (
    ForeignHolding,
    ShareholdingDistribution,
    ShareholdingPair,
)
from quant_signal.infrastructure.column_limits import enforce_column_limits
from quant_signal.infrastructure.db.models import ForeignHoldingRow, ShareholdingDistributionRow
from quant_signal.quant.holders import PAIR_WINDOW, pair_snapshots


class MemoryHoldersMixin:
    shareholding_rows: dict[tuple[str, object], ShareholdingDistribution]
    foreign_rows: dict[tuple[str, object, str], ForeignHolding]

    def _holders(self) -> None:
        if not hasattr(self, "shareholding_rows"):
            self.shareholding_rows = {}
            self.foreign_rows = {}

    async def upsert_shareholding_distribution(self, rows: list[ShareholdingDistribution]) -> int:
        self._holders()
        written = 0
        for row in rows:
            key = (row.symbol, row.data_date)
            if key not in self.shareholding_rows:
                enforce_column_limits(ShareholdingDistributionRow, row.model_dump())
                self.shareholding_rows[key] = row
                written += 1
        return written

    async def upsert_foreign_holding(self, rows: list[ForeignHolding]) -> int:
        self._holders()
        written = 0
        for row in rows:
            key = (row.symbol, row.trading_date, row.source)
            if key not in self.foreign_rows:
                enforce_column_limits(ForeignHoldingRow, row.model_dump())
                self.foreign_rows[key] = row
                written += 1
        return written

    async def has_shareholding_distribution(self) -> bool:
        self._holders()
        return bool(self.shareholding_rows)

    def _visible_weeks(self, as_of: datetime) -> list[ShareholdingDistribution]:
        self._holders()
        return [row for row in self.shareholding_rows.values() if row.available_at <= as_of]

    async def latest_shareholding_pairs(self, as_of: datetime) -> dict[str, ShareholdingPair]:
        visible = self._visible_weeks(as_of)
        if not visible:
            return {}
        newest = max(row.data_date for row in visible)
        by_symbol: dict[str, list] = {}
        for row in visible:
            if newest - PAIR_WINDOW <= row.data_date <= newest:
                by_symbol.setdefault(row.symbol, []).append(row.snapshot())
        pairs = {symbol: pair_snapshots(rows) for symbol, rows in by_symbol.items()}
        return {symbol: pair for symbol, pair in sorted(pairs.items()) if pair is not None}

    async def latest_foreign_holdings(self, as_of: datetime) -> dict[str, ForeignHolding]:
        self._holders()
        best: dict[str, ForeignHolding] = {}
        for row in self.foreign_rows.values():
            if row.available_at > as_of:
                continue
            current = best.get(row.symbol)
            if current is None or (row.trading_date, row.available_at) > (
                current.trading_date,
                current.available_at,
            ):
                best[row.symbol] = row
        return dict(sorted(best.items()))

    async def latest_foreign_holding(self, symbol: str, as_of: datetime) -> ForeignHolding | None:
        return (await self.latest_foreign_holdings(as_of)).get(symbol.upper())

    async def shareholding_history(
        self, symbol: str, as_of: datetime, weeks: int = 26
    ) -> list[ShareholdingDistribution]:
        rows = [row for row in self._visible_weeks(as_of) if row.symbol == symbol.upper()]
        return sorted(rows, key=lambda row: row.data_date)[-weeks:]
