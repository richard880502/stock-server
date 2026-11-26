"""In-memory twin of ``db/official_repository.py`` (tests and demo mode)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from quant_signal.domain.models import MarketEvent, MonthlyRevenue, RiskFlag
from quant_signal.infrastructure.revisions import latest_visible, plan_revisions

TAIPEI = ZoneInfo("Asia/Taipei")


def _event_key(row: MarketEvent) -> tuple:
    return (row.symbol, row.kind, row.external_key)


def _flag_key(row: RiskFlag) -> tuple:
    return (row.symbol, row.kind, row.start_date, row.source)


def _revenue_key(row: MonthlyRevenue) -> tuple:
    return (row.symbol, row.period)


class MemoryOfficialDataMixin:
    market_events: list[MarketEvent]
    risk_flags: list[RiskFlag]
    revenue_rows: list[MonthlyRevenue]

    def _official(self) -> None:
        if not hasattr(self, "market_events"):
            self.market_events = []
            self.risk_flags = []
            self.revenue_rows = []

    @staticmethod
    def _latest(rows: list, key) -> dict:
        best: dict = {}
        for row in rows:
            current = best.get(key(row))
            if current is None or row.revision > current.revision:
                best[key(row)] = row
        return best

    async def upsert_market_events(self, rows: list[MarketEvent]) -> int:
        self._official()
        planned = plan_revisions(
            self._latest(self.market_events, _event_key),
            rows,
            _event_key,
            ignore={"announced_at"},
            carry=("announced_at",),
        )
        self.market_events.extend(planned)
        return len(planned)

    async def upsert_risk_flags(self, rows: list[RiskFlag]) -> int:
        self._official()
        planned = plan_revisions(self._latest(self.risk_flags, _flag_key), rows, _flag_key)
        self.risk_flags.extend(planned)
        return len(planned)

    async def upsert_monthly_revenue(self, rows: list[MonthlyRevenue]) -> int:
        self._official()
        planned = plan_revisions(self._latest(self.revenue_rows, _revenue_key), rows, _revenue_key)
        self.revenue_rows.extend(planned)
        return len(planned)

    async def list_market_events(
        self, symbols: list[str] | None, since: datetime, as_of: datetime
    ) -> list[MarketEvent]:
        self._official()
        wanted = {s.upper() for s in symbols} if symbols is not None else None
        since_day = since.astimezone(TAIPEI).date()
        result = []
        for event in latest_visible(self.market_events, _event_key, as_of).values():
            if wanted is not None and event.symbol not in wanted:
                continue
            if event.kind == "ex_rights":
                keep = event.effective_date is not None and event.effective_date >= since_day
            else:
                keep = since <= event.announced_at <= as_of
            if keep:
                result.append(event)
        return sorted(result, key=lambda e: (-e.announced_at.timestamp(), e.symbol))

    async def active_risk_flags(
        self, as_of: datetime, symbols: list[str] | None = None
    ) -> list[RiskFlag]:
        self._official()
        wanted = {s.upper() for s in symbols} if symbols is not None else None
        day = as_of.astimezone(TAIPEI).date()
        result = []
        for flag in latest_visible(self.risk_flags, _flag_key, as_of).values():
            if wanted is not None and flag.symbol not in wanted:
                continue
            if flag.start_date <= day and (flag.end_date is None or flag.end_date >= day):
                result.append(flag)
        return sorted(result, key=lambda f: (f.symbol, f.kind, f.start_date))

    async def monthly_revenue(
        self, symbol: str, as_of: datetime, months: int = 13
    ) -> list[MonthlyRevenue]:
        self._official()
        visible = [
            row
            for row in latest_visible(self.revenue_rows, _revenue_key, as_of).values()
            if row.symbol == symbol.upper()
        ]
        return sorted(visible, key=lambda r: r.period)[-months:]

    async def latest_monthly_revenue(self, as_of: datetime) -> list[MonthlyRevenue]:
        self._official()
        newest: dict[str, MonthlyRevenue] = {}
        for row in latest_visible(self.revenue_rows, _revenue_key, as_of).values():
            if row.symbol not in newest or row.period > newest[row.symbol].period:
                newest[row.symbol] = row
        return sorted(newest.values(), key=lambda r: r.symbol)
