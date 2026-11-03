"""Shared fakes: a provider that never touches the network and records its calls."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from quant_signal.domain.models import Bar, CorporateAction, InstitutionalFlow


def _weekdays(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


class FakeProvider:
    """Serves synthetic bars for any requested range and records every call."""

    def __init__(self, actions: list[CorporateAction] | None = None) -> None:
        self.instrument_names: dict[str, str] = {}
        self.actions = actions or []
        self.bar_calls: list[tuple[date, date]] = []
        self.action_calls: list[tuple[date, date]] = []
        self.flow_calls: list[tuple[date, date]] = []
        self.empty_before: date | None = None

    async def fetch_symbol_bars(self, symbol: str, *, start: date, end: date) -> list[Bar]:
        self.bar_calls.append((start, end))
        self.instrument_names[symbol] = "TSMC"
        days = _weekdays(start, end)
        if self.empty_before:
            days = [day for day in days if day >= self.empty_before]
        return [
            Bar(
                symbol=symbol,
                trading_date=day,
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1_000,
                available_at=datetime.combine(day, time(6), tzinfo=UTC),
                source="twse_official",
            )
            for day in days
        ]

    async def fetch_index_bars(self, *, start: date, end: date) -> list[Bar]:
        return await self.fetch_symbol_bars("^TWII", start=start, end=end)

    async def fetch_corporate_actions(
        self, symbol: str, *, start: date, end: date
    ) -> list[CorporateAction]:
        self.action_calls.append((start, end))
        return [a for a in self.actions if start <= a.ex_date <= end]

    async def fetch_institutional_flows(
        self, symbol: str, *, start: date, end: date, concurrency: int = 4
    ) -> list[InstitutionalFlow]:
        self.flow_calls.append((start, end))
        return [
            InstitutionalFlow(
                symbol=symbol,
                trading_date=day,
                foreign_buy=2,
                foreign_sell=1,
                foreign_net=1,
                investment_trust_buy=0,
                investment_trust_sell=0,
                investment_trust_net=0,
                dealer_buy=0,
                dealer_sell=0,
                dealer_net=0,
                total_net=1,
                available_at=datetime.combine(day, time(11), tzinfo=UTC),
                source="twse_official",
            )
            for day in _weekdays(start, end)
        ]
