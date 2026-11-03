from __future__ import annotations

from datetime import UTC, date, datetime, time

from fakes import FakeProvider, _weekdays

from quant_signal.application.data_sync import TwseDataSyncService
from quant_signal.domain.models import Bar, CorporateAction
from quant_signal.infrastructure.memory import MemoryQuantRepository

SYMBOL = "2330.TW"


def _clock(moment: datetime):
    state = {"now": moment}

    def now() -> datetime:
        return state["now"]

    now.set = lambda value: state.update(now=value)  # type: ignore[attr-defined]
    return now


def _at(day: date, hour: int = 16) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=UTC)


def _action(ex_date: date) -> CorporateAction:
    return CorporateAction(
        symbol=SYMBOL,
        ex_date=ex_date,
        previous_close=100,
        reference_price=90,
        event_type="dividend",
        available_at=_at(ex_date, 1),
        source="twse_official",
    )


async def test_closed_months_are_fetched_once_and_open_month_is_refreshed() -> None:
    repository = MemoryQuantRepository()
    provider = FakeProvider()
    clock = _clock(_at(date(2026, 7, 15)))
    service = TwseDataSyncService(repository, provider, clock=clock)

    first = await service.sync_symbol(SYMBOL, start=date(2026, 4, 10), end=date(2026, 7, 15))
    clock.set(_at(date(2026, 7, 16)))
    second = await service.sync_symbol(SYMBOL, start=date(2026, 4, 10), end=date(2026, 7, 16))

    assert first.periods_fetched == 4 and first.periods_skipped == 0
    assert second.periods_fetched == 1 and second.periods_skipped == 3
    # The second request only touches July, from its first day.
    assert provider.bar_calls == [
        (date(2026, 4, 1), date(2026, 7, 15)),
        (date(2026, 7, 1), date(2026, 7, 16)),
    ]


async def test_a_month_synced_while_still_open_is_refetched_after_it_closes() -> None:
    repository = MemoryQuantRepository()
    provider = FakeProvider()
    clock = _clock(_at(date(2026, 7, 15)))
    service = TwseDataSyncService(repository, provider, clock=clock)

    await service.sync_symbol(SYMBOL, start=date(2026, 7, 1), end=date(2026, 7, 15))
    clock.set(_at(date(2026, 8, 3)))
    report = await service.sync_symbol(SYMBOL, start=date(2026, 7, 1), end=date(2026, 8, 3))

    assert report.periods_skipped == 0  # July was only seen mid-month
    clock.set(_at(date(2026, 8, 4)))
    again = await service.sync_symbol(SYMBOL, start=date(2026, 7, 1), end=date(2026, 8, 4))
    assert again.periods_skipped == 1  # July is now complete and stored


async def test_empty_months_are_remembered_and_not_requested_again() -> None:
    repository = MemoryQuantRepository()
    provider = FakeProvider()
    provider.empty_before = date(2026, 6, 1)  # listed in June
    clock = _clock(_at(date(2026, 7, 15)))
    service = TwseDataSyncService(repository, provider, clock=clock)

    await service.sync_symbol(SYMBOL, start=date(2026, 4, 1), end=date(2026, 7, 15))
    clock.set(_at(date(2026, 7, 16)))
    provider.bar_calls.clear()
    await service.sync_symbol(SYMBOL, start=date(2026, 4, 1), end=date(2026, 7, 16))

    assert provider.bar_calls == [(date(2026, 7, 1), date(2026, 7, 16))]


async def test_a_past_month_cut_short_by_end_is_not_marked_complete() -> None:
    repository = MemoryQuantRepository()
    provider = FakeProvider()
    service = TwseDataSyncService(
        repository, provider, clock=_clock(_at(date(2026, 7, 15)))
    )

    await service.sync_symbol(SYMBOL, start=date(2026, 3, 1), end=date(2026, 3, 10))
    provider.bar_calls.clear()
    report = await service.sync_symbol(SYMBOL, start=date(2026, 3, 1), end=date(2026, 3, 31))

    assert report.periods_skipped == 0
    assert provider.bar_calls == [(date(2026, 3, 1), date(2026, 3, 31))]


async def test_adjustment_factor_resumes_from_the_last_stored_bar() -> None:
    repository = MemoryQuantRepository()
    provider = FakeProvider(actions=[_action(date(2026, 5, 6))])
    clock = _clock(_at(date(2026, 7, 15)))
    service = TwseDataSyncService(repository, provider, clock=clock)

    await service.sync_symbol(SYMBOL, start=date(2026, 4, 1), end=date(2026, 7, 15))
    clock.set(_at(date(2026, 7, 16)))
    provider.action_calls.clear()
    await service.sync_symbol(SYMBOL, start=date(2026, 4, 1), end=date(2026, 7, 16))

    factor = 100 / 90
    bars = {bar.trading_date: bar for bar in await repository.list_bars(SYMBOL)}
    assert bars[date(2026, 5, 5)].adjustment_factor == 1.0
    assert bars[date(2026, 5, 6)].adjustment_factor == factor
    # July came from a later, narrower request yet carries the May event.
    assert bars[date(2026, 7, 16)].adjustment_factor == factor
    # That request asked the exchange only for events since July started.
    assert provider.action_calls == [(date(2026, 7, 1), date(2026, 7, 16))]


async def test_a_new_event_in_the_open_month_adjusts_only_from_its_ex_date() -> None:
    repository = MemoryQuantRepository()
    provider = FakeProvider()
    clock = _clock(_at(date(2026, 7, 15)))
    service = TwseDataSyncService(repository, provider, clock=clock)
    await service.sync_symbol(SYMBOL, start=date(2026, 6, 1), end=date(2026, 7, 15))

    provider.actions = [_action(date(2026, 7, 14))]
    clock.set(_at(date(2026, 7, 16)))
    await service.sync_symbol(SYMBOL, start=date(2026, 6, 1), end=date(2026, 7, 16))

    bars = {bar.trading_date: bar for bar in await repository.list_bars(SYMBOL)}
    assert bars[date(2026, 6, 30)].adjustment_factor == 1.0
    assert bars[date(2026, 7, 13)].adjustment_factor == 1.0
    assert bars[date(2026, 7, 14)].adjustment_factor == 100 / 90


async def test_index_sync_skips_closed_months_without_corporate_actions() -> None:
    repository = MemoryQuantRepository()
    provider = FakeProvider()
    clock = _clock(_at(date(2026, 7, 15)))
    service = TwseDataSyncService(repository, provider, clock=clock)

    await service.sync_symbol("^TWII", start=date(2026, 5, 1), end=date(2026, 7, 15))
    clock.set(_at(date(2026, 7, 16)))
    report = await service.sync_symbol("^TWII", start=date(2026, 5, 1), end=date(2026, 7, 16))

    assert report.periods_skipped == 2
    assert provider.action_calls == []


async def test_institutional_flow_days_are_not_refetched() -> None:
    repository = MemoryQuantRepository()
    provider = FakeProvider()
    clock = _clock(_at(date(2026, 7, 15)))
    service = TwseDataSyncService(repository, provider, clock=clock)

    first = await service.sync_institutional_flows(
        SYMBOL, start=date(2026, 7, 6), end=date(2026, 7, 10)
    )
    clock.set(_at(date(2026, 7, 16)))
    second = await service.sync_institutional_flows(
        SYMBOL, start=date(2026, 7, 6), end=date(2026, 7, 15)
    )

    assert first.periods_fetched == 5
    assert second.periods_skipped == 5
    # Only the days after the earlier run hit the exchange (Fri -> Mon is one run).
    assert provider.flow_calls == [
        (date(2026, 7, 6), date(2026, 7, 10)),
        (date(2026, 7, 13), date(2026, 7, 15)),
    ]


async def test_resume_ignores_non_exchange_bars_from_the_previous_month() -> None:
    repository = MemoryQuantRepository()
    provider = FakeProvider(actions=[_action(date(2026, 5, 6))])
    clock = _clock(_at(date(2026, 7, 15)))
    service = TwseDataSyncService(repository, provider, clock=clock)
    await service.sync_symbol(SYMBOL, start=date(2026, 4, 1), end=date(2026, 7, 15))

    # A demo bar sits on the last June session with a meaningless factor of 1.
    june_last = max(
        bar.trading_date
        for bar in await repository.list_bars(SYMBOL)
        if bar.trading_date < date(2026, 7, 1)
    )
    await repository.upsert_bars(
        [
            Bar(
                symbol=SYMBOL,
                trading_date=june_last,
                open=100,
                high=101,
                low=99,
                close=100,
                adjusted_close=100,
                adjustment_factor=1.0,
                volume=1,
                available_at=_at(june_last, 1),
                source="synthetic_demo",
            )
        ]
    )
    clock.set(_at(date(2026, 7, 16)))
    await service.sync_symbol(SYMBOL, start=date(2026, 4, 1), end=date(2026, 7, 16))

    july = [
        bar
        for bar in await repository.list_bars(SYMBOL)
        if bar.trading_date >= date(2026, 7, 1) and bar.source == "twse_official"
    ]
    assert {round(bar.adjustment_factor, 9) for bar in july} == {round(100 / 90, 9)}


async def test_legacy_bars_without_coverage_are_rebuilt_on_one_basis() -> None:
    repository = MemoryQuantRepository()
    # Rows an older sync left behind: factor 1.0 with an adjusted close filled in,
    # but no coverage record, so nothing proves their factors are complete.
    legacy_days = [d for d in _weekdays(date(2026, 1, 1), date(2026, 3, 31))]
    await repository.upsert_bars(
        [
            Bar(
                symbol=SYMBOL,
                trading_date=day,
                open=100,
                high=101,
                low=99,
                close=100,
                adjusted_close=100,
                adjustment_factor=1.0,
                volume=1_000,
                available_at=_at(day, 1),
                source="twse_official",
            )
            for day in legacy_days
        ]
    )
    provider = FakeProvider(actions=[_action(date(2026, 2, 10))])
    clock = _clock(_at(date(2026, 5, 15)))
    service = TwseDataSyncService(repository, provider, clock=clock)

    await service.sync_symbol(SYMBOL, start=date(2026, 4, 1), end=date(2026, 5, 15))

    factor = 100 / 90
    bars = {bar.trading_date: bar for bar in await repository.list_bars(SYMBOL)}
    assert bars[date(2026, 2, 9)].adjustment_factor == 1.0
    assert bars[date(2026, 2, 10)].adjustment_factor == factor
    # The old rows were rebuilt, and April continues from them instead of restarting at 1.
    assert bars[date(2026, 3, 31)].adjustment_factor == factor
    assert bars[date(2026, 4, 1)].adjustment_factor == factor
    assert bars[date(2026, 5, 15)].adjustment_factor == factor
    assert provider.bar_calls[0][0] == date(2026, 1, 1)
    # Corporate actions were requested from the beginning, not just from April.
    assert provider.action_calls[0][0] < date(2026, 1, 1)

    # Once recorded, the rebuilt months are trusted and not fetched again.
    provider.bar_calls.clear()
    clock.set(_at(date(2026, 5, 16)))
    await service.sync_symbol(SYMBOL, start=date(2026, 4, 1), end=date(2026, 5, 16))
    assert provider.bar_calls == [(date(2026, 5, 1), date(2026, 5, 16))]
