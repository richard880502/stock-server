"""In-memory twin of ``db/fundamentals_repository.py`` (tests and demo mode)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from quant_signal.domain.fundamentals import (
    PERCENTILE_WINDOW,
    EarningsMetrics,
    QuarterlyFinancial,
    RevenueTrend,
    ValuationDay,
    ValuationMetrics,
)
from quant_signal.domain.models import MonthlyRevenue
from quant_signal.infrastructure.revisions import latest_visible, plan_revisions
from quant_signal.quant.quarterly import earnings_metrics
from quant_signal.quant.revenue_trend import revenue_trend
from quant_signal.quant.valuation import valuation_metrics

TAIPEI = ZoneInfo("Asia/Taipei")
VALUATION_LOOKBACK_DAYS = 430
REVENUE_LOOKBACK_MONTHS = 40
FINANCIAL_LOOKBACK_YEARS = 3


def _financial_key(row: QuarterlyFinancial) -> tuple:
    return (row.symbol, row.fiscal_year, row.fiscal_quarter)


class MemoryFundamentalsMixin:
    valuation_rows: dict[tuple[str, object, str], ValuationDay]
    financial_rows: list[QuarterlyFinancial]

    def _fundamentals(self) -> None:
        if not hasattr(self, "valuation_rows"):
            self.valuation_rows = {}
            self.financial_rows = []

    async def upsert_valuation_daily(self, rows: list[ValuationDay]) -> int:
        self._fundamentals()
        written = 0
        for row in rows:
            key = (row.symbol, row.trading_date, row.source)
            if key not in self.valuation_rows:
                self.valuation_rows[key] = row
                written += 1
        return written

    async def latest_valuation_metrics(
        self,
        as_of: datetime,
        symbols: list[str] | None = None,
        *,
        window: int = PERCENTILE_WINDOW,
    ) -> dict[str, ValuationMetrics]:
        self._fundamentals()
        wanted = {symbol.upper() for symbol in symbols} if symbols is not None else None
        day = as_of.astimezone(TAIPEI).date()
        floor = day - timedelta(days=VALUATION_LOOKBACK_DAYS)
        by_symbol: dict[str, list[ValuationDay]] = {}
        for row in self.valuation_rows.values():
            if row.available_at > as_of or not floor <= row.trading_date <= day:
                continue
            if wanted is None or row.symbol in wanted:
                by_symbol.setdefault(row.symbol, []).append(row)
        result: dict[str, ValuationMetrics] = {}
        for symbol in sorted(by_symbol):
            metrics = valuation_metrics(by_symbol[symbol], window=window)
            if metrics is not None:
                result[symbol] = metrics
        return result

    async def valuation_history(
        self, symbol: str, as_of: datetime, sessions: int = 300
    ) -> list[ValuationDay]:
        self._fundamentals()
        rows = [
            row
            for row in self.valuation_rows.values()
            if row.symbol == symbol.upper() and row.available_at <= as_of
        ]
        return sorted(rows, key=lambda row: row.trading_date)[-sessions:]

    async def insert_missing_monthly_revenue(self, rows: list[MonthlyRevenue]) -> int:
        self._official()  # type: ignore[attr-defined]
        held = {(row.symbol, row.period) for row in self.revenue_rows}  # type: ignore[attr-defined]
        fresh: dict[tuple[str, str], MonthlyRevenue] = {}
        for row in rows:
            if (row.symbol, row.period) not in held:
                fresh[(row.symbol, row.period)] = row
        self.revenue_rows.extend(fresh.values())  # type: ignore[attr-defined]
        return len(fresh)

    async def revenue_trends(
        self, as_of: datetime, symbols: list[str] | None = None, *, include_months: bool = False
    ) -> dict[str, RevenueTrend]:
        self._official()  # type: ignore[attr-defined]
        wanted = {symbol.upper() for symbol in symbols} if symbols is not None else None
        day = as_of.astimezone(TAIPEI).date()
        index = day.year * 12 + day.month - 1 - REVENUE_LOOKBACK_MONTHS
        floor = f"{index // 12:04d}-{index % 12 + 1:02d}"
        by_symbol: dict[str, list[MonthlyRevenue]] = {}
        visible = latest_visible(
            self.revenue_rows,  # type: ignore[attr-defined]
            lambda row: (row.symbol, row.period),
            as_of,
        )
        for row in visible.values():
            if row.period >= floor and (wanted is None or row.symbol in wanted):
                by_symbol.setdefault(row.symbol, []).append(row)
        result: dict[str, RevenueTrend] = {}
        for symbol in sorted(by_symbol):
            trend = revenue_trend(by_symbol[symbol], include_months=include_months)
            if trend is not None:
                result[symbol] = trend
        return result

    def _latest_financials(self) -> dict[tuple, QuarterlyFinancial]:
        best: dict[tuple, QuarterlyFinancial] = {}
        for row in self.financial_rows:
            key = _financial_key(row)
            if key not in best or row.revision > best[key].revision:
                best[key] = row
        return best

    async def upsert_quarterly_financials(self, rows: list[QuarterlyFinancial]) -> int:
        self._fundamentals()
        planned = plan_revisions(self._latest_financials(), rows, _financial_key, ignore={"source"})
        self.financial_rows.extend(planned)
        return len(planned)

    async def insert_missing_quarterly_financials(self, rows: list[QuarterlyFinancial]) -> int:
        self._fundamentals()
        held = {_financial_key(row) for row in self.financial_rows}
        fresh: dict[tuple, QuarterlyFinancial] = {}
        for row in rows:
            if _financial_key(row) not in held:
                fresh[_financial_key(row)] = row
        self.financial_rows.extend(fresh.values())
        return len(fresh)

    def _visible_financials(
        self, as_of: datetime, symbols: list[str] | None
    ) -> dict[str, list[QuarterlyFinancial]]:
        self._fundamentals()
        wanted = {symbol.upper() for symbol in symbols} if symbols is not None else None
        floor = as_of.astimezone(TAIPEI).year - FINANCIAL_LOOKBACK_YEARS
        by_symbol: dict[str, list[QuarterlyFinancial]] = {}
        for row in latest_visible(self.financial_rows, _financial_key, as_of).values():
            if row.fiscal_year >= floor and (wanted is None or row.symbol in wanted):
                by_symbol.setdefault(row.symbol, []).append(row)
        return by_symbol

    async def quarterly_financials(self, symbol: str, as_of: datetime) -> list[QuarterlyFinancial]:
        rows = self._visible_financials(as_of, [symbol]).get(symbol.upper(), [])
        return sorted(rows, key=lambda row: (row.fiscal_year, row.fiscal_quarter))

    async def earnings_summaries(
        self, as_of: datetime, symbols: list[str] | None = None
    ) -> dict[str, EarningsMetrics]:
        by_symbol = self._visible_financials(as_of, symbols)
        result: dict[str, EarningsMetrics] = {}
        for symbol in sorted(by_symbol):
            metrics = earnings_metrics(by_symbol[symbol])
            if metrics is not None:
                result[symbol] = metrics
        return result
