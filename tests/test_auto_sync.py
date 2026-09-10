from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

import quant_signal.application.data_sync as data_sync_module
from quant_signal.application.data_sync import ensure_symbol_bar_coverage
from quant_signal.application.services import SignalService
from quant_signal.domain.models import Bar
from quant_signal.infrastructure.memory import MemoryQuantRepository


def _fake_bars(symbol: str, count: int, start: date) -> list[Bar]:
    bars: list[Bar] = []
    current = start
    price = 100.0
    while len(bars) < count:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue
        bars.append(
            Bar(
                symbol=symbol,
                trading_date=current,
                open=price,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                adjusted_close=price,
                volume=1_000_000,
                available_at=datetime.combine(current, time(hour=6), tzinfo=UTC),
                source="twse_official",
            )
        )
        current += timedelta(days=1)
    return bars


class FakeTwseProvider:
    """Stands in for TwseProvider so tests never touch the real network."""

    instrument_names: dict[str, str] = {}
    calls = 0

    def __init__(self) -> None:
        pass

    async def __aenter__(self) -> FakeTwseProvider:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def fetch_symbol_bars(self, symbol: str, *, start: date, end: date) -> list[Bar]:
        FakeTwseProvider.calls += 1
        return _fake_bars(symbol, 90, start)

    async def fetch_corporate_actions(self, symbol: str, *, start: date, end: date) -> list:
        return []


async def test_ensure_symbol_bar_coverage_skips_when_already_sufficient(
    trending_bars: list[Bar],
) -> None:
    repository = MemoryQuantRepository()
    await repository.upsert_bars(trending_bars)
    FakeTwseProvider.calls = 0

    performed = await ensure_symbol_bar_coverage(
        repository, "TEST", as_of=trending_bars[-1].trading_date
    )

    assert performed is False
    assert FakeTwseProvider.calls == 0


async def test_ensure_symbol_bar_coverage_syncs_when_insufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(data_sync_module, "TwseProvider", FakeTwseProvider)
    repository = MemoryQuantRepository()
    FakeTwseProvider.calls = 0

    performed = await ensure_symbol_bar_coverage(
        repository, "2330.TW", as_of=date(2026, 7, 31)
    )

    assert performed is True
    assert FakeTwseProvider.calls == 1
    stored = await repository.list_bars("2330.TW", end=date(2026, 7, 31))
    assert len(stored) >= 60


async def test_signal_service_default_never_attempts_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(data_sync_module, "TwseProvider", FakeTwseProvider)
    FakeTwseProvider.calls = 0
    repository = MemoryQuantRepository()

    with pytest.raises(ValueError):
        await SignalService(repository).analyze("2330.TW", as_of=date(2026, 7, 31))

    assert FakeTwseProvider.calls == 0


async def test_signal_service_auto_sync_backfills_before_analyzing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(data_sync_module, "TwseProvider", FakeTwseProvider)
    FakeTwseProvider.calls = 0
    repository = MemoryQuantRepository()

    result = await SignalService(repository, auto_sync=True).analyze(
        "2330.TW", as_of=date(2026, 7, 31)
    )

    assert result.symbol == "2330.TW"
    assert FakeTwseProvider.calls == 1
