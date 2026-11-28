"""In-memory twin of ``db/etf_repository.py``."""

from __future__ import annotations

from datetime import date, timedelta

from quant_signal.domain.etf import EtfDay
from quant_signal.domain.models import Instrument
from quant_signal.infrastructure.column_limits import enforce_column_limits
from quant_signal.infrastructure.db.etf_models import EtfDailyRow
from quant_signal.infrastructure.db.etf_repository import ETF_SEARCH_WINDOW_DAYS


class MemoryEtfMixin:
    @property
    def etf_days(self) -> dict[tuple[str, date], EtfDay]:
        return self.__dict__.setdefault("_etf_days", {})

    async def upsert_etf_days(self, rows: list[EtfDay]) -> int:
        for item in rows:
            enforce_column_limits(EtfDailyRow, item.model_dump())
        for item in rows:
            self.etf_days[(item.symbol, item.trading_date)] = item
        return len(rows)

    async def list_etf_rows(
        self, symbols: list[str], *, as_of: date | None = None, sessions: int = 3
    ) -> list[EtfDay]:
        wanted = {item.upper() for item in symbols}
        by_symbol: dict[str, list[EtfDay]] = {}
        for item in self.etf_days.values():
            if item.symbol in wanted and (as_of is None or item.trading_date <= as_of):
                by_symbol.setdefault(item.symbol, []).append(item)
        found: list[EtfDay] = []
        for symbol in sorted(by_symbol):
            found.extend(sorted(by_symbol[symbol], key=lambda r: r.trading_date)[-sessions:])
        return found if sessions >= 1 else []

    async def search_etf_instruments(self, query: str, *, limit: int = 10) -> list[Instrument]:
        needle = query.strip().lower()
        if not needle or not self.etf_days:
            return []
        newest = max(item.trading_date for item in self.etf_days.values())
        cutoff = newest - timedelta(days=ETF_SEARCH_WINDOW_DAYS)
        latest: dict[str, EtfDay] = {}
        for item in self.etf_days.values():
            if item.trading_date < cutoff:
                continue
            if needle not in item.symbol.lower() and needle not in item.name.lower():
                continue
            current = latest.get(item.symbol)
            if current is None or item.trading_date > current.trading_date:
                latest[item.symbol] = item
        return [
            Instrument(symbol=i.symbol, name=i.name, market=i.market, tracked=False, kind="etf")
            for _, i in sorted(latest.items())
        ][: max(limit, 1) * 5]
