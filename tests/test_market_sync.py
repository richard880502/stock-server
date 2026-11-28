from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from fakes import FakeMarketProvider

from quant_signal.application.market_sync import MarketSnapshotSyncService
from quant_signal.application.sync_jobs import (
    enqueue_daily_refresh,
    enqueue_market_repairs,
    process_next_job,
)
from quant_signal.domain.models import Instrument, JobStatus, SyncJobKind
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.infrastructure.providers.market_snapshot import (
    StockCredit,
    StockFlow,
    build_market_stock_days,
)
from quant_signal.infrastructure.providers.tpex import TpexProvider
from quant_signal.infrastructure.providers.twse import TwseProvider, TwseSecurityQuote

TWSE_FLOW = {
    "stat": "OK",
    "data": [
        # code, name, then foreign (3), foreign dealer (3), trust(3)...
        [
            "2330",
            "台積電",
            "1",
            "2",
            "-15,005,014",
            "0",
            "0",
            "0",
            "100",
            "50",
            "3,682,223",
            "1,885,893",
            "10",
            "5",
            "5",
            "8",
            "4",
            "4",
            "-9,436,898",
        ],
    ],
}
TWSE_CREDIT = {
    "stat": "OK",
    "tables": [
        {"fields": ["項目", "買進"], "data": [["融資(交易單位)", "1"]]},
        {
            "fields": ["代號", "名稱"],
            "data": [
                [
                    "2330",
                    "台積電",
                    "884",
                    "608",
                    "18",
                    "34,961",
                    "35,219",
                    "6,483,092",
                    "4",
                    "8",
                    "0",
                    "9",
                    "13",
                    "6,483,092",
                    "2",
                    " ",
                ],
                ["2317", "鴻海", "1", "1", "0", "10", "--"] + ["0"] * 9,
            ],
        },
    ],
}


def _tpex_flow_row() -> list[str]:
    row = ["0"] * 24
    row[0], row[1] = "6488", "環球晶"
    row[4], row[13], row[22], row[23] = "-90,304", "-1,202,788", "197,605", "-1,095,487"
    return row


TPEX_FLOW = {"tables": [{"data": [_tpex_flow_row()]}]}
TPEX_CREDIT = {
    "tables": [
        {
            "data": [
                [
                    "6488",
                    "環球晶",
                    "11,440",
                    "200",
                    "47",
                    "0",
                    "11,593",
                    "0",
                    "5.0",
                    "100",
                    "300",
                    "10",
                    "8",
                    "0",
                    "218",
                    "0",
                    "0",
                    "0",
                    "0",
                    "",
                ],
            ]
        }
    ]
}


def test_twse_flow_and_credit_parsing() -> None:
    flows = TwseProvider.parse_all_stock_flows(TWSE_FLOW)
    credit = TwseProvider.parse_all_stock_credit(TWSE_CREDIT)

    assert flows["2330"] == StockFlow(
        foreign_net=-15_005_014, trust_net=3_682_223, dealer_net=1_885_893, total_net=-9_436_898
    )
    assert credit["2330"] == StockCredit(
        margin_balance=35_219, margin_change=258, short_balance=13, short_change=4
    )
    # A row with an unreadable balance is dropped, never guessed.
    assert "2317" not in credit
    assert TwseProvider.parse_all_stock_flows({"stat": "很抱歉"}) == {}


def test_tpex_flow_and_credit_parsing() -> None:
    flows = TpexProvider.parse_all_stock_flows(TPEX_FLOW)
    credit = TpexProvider.parse_all_stock_credit(TPEX_CREDIT)

    assert flows["6488"].foreign_net == -90_304
    assert flows["6488"].trust_net == -1_202_788
    assert flows["6488"].total_net == -1_095_487
    assert credit["6488"] == StockCredit(
        margin_balance=11_593, margin_change=153, short_balance=218, short_change=-82
    )


def _quote(code: str) -> TwseSecurityQuote:
    return TwseSecurityQuote(
        symbol=f"{code}.TW",
        name=code,
        trading_date=date(2026, 11, 2),
        open=10,
        high=11,
        low=9,
        close=10,
        volume=1000,
        turnover_value=10_000,
        direction=1,
        change=0.5,
    )


def _build(flows, credit):
    return build_market_stock_days(
        [_quote("1101"), _quote("1102")],
        market="TW",
        source="twse_official",
        available_at=datetime(2026, 11, 2, 10, tzinfo=UTC),
        flows=flows,
        credit=credit,
        trading_date=date(2026, 11, 2),
    )


def test_a_stock_missing_from_a_published_flow_report_nets_to_zero() -> None:
    rows = {r.symbol: r for r in _build({"1101": StockFlow(5, 6, 7, 18)}, {})}

    assert rows["1101.TW"].total_net == 18
    assert rows["1102.TW"].total_net == 0 and rows["1102.TW"].foreign_net == 0
    # Credit is only known for stocks the exchange lists as margin-eligible.
    assert rows["1101.TW"].margin_balance is None


def test_an_unpublished_flow_report_leaves_flows_unknown() -> None:
    rows = _build({}, {})

    assert all(r.total_net is None and r.foreign_net is None for r in rows)
    assert rows[0].change == 0.5


def _at(day: date, hour: int = 8) -> datetime:  # 08:00 UTC = 16:00 Taipei
    return datetime.combine(day, time(hour), tzinfo=UTC)


async def test_snapshot_sync_fetches_each_day_once_and_remembers_holidays() -> None:
    repository = MemoryQuantRepository()
    holiday = date(2026, 10, 14)
    provider = FakeMarketProvider(holidays={holiday})
    now = {"value": _at(date(2026, 10, 16))}
    service = MarketSnapshotSyncService(
        repository, provider, market="TW", source="twse_official", clock=lambda: now["value"]
    )

    first = await service.sync(start=date(2026, 10, 12), end=date(2026, 10, 16))
    assert first.periods_fetched == 5 and first.market_observations_inserted == 8
    assert provider.calls == sorted(provider.calls, reverse=True)  # newest first

    provider.calls.clear()
    now["value"] = _at(date(2026, 10, 17))
    second = await service.sync(start=date(2026, 10, 12), end=date(2026, 10, 16))

    # Days fetched before they ended are refetched; nothing older is.
    assert second.periods_skipped == 4 and provider.calls == [date(2026, 10, 16)]

    provider.calls.clear()
    now["value"] = _at(date(2026, 10, 20))
    third = await service.sync(start=date(2026, 10, 12), end=date(2026, 10, 16))
    assert third.periods_fetched == 0 and provider.calls == []
    dates = await repository.list_market_trading_dates(None, as_of=None, limit=10)
    assert holiday not in dates and len(dates) == 4


async def test_worker_runs_a_market_job_for_the_right_exchange() -> None:
    repository = MemoryQuantRepository()
    twse, tpex = FakeMarketProvider(), FakeMarketProvider()
    job, _ = await repository.enqueue_sync_job(SyncJobKind.MARKET, "TW", lookback_days=7)

    outcome = await process_next_job(
        repository,
        twse,
        tpex,
        max_attempts=2,
        today=date(2026, 10, 16),  # type: ignore[arg-type]
    )

    assert outcome is True and repository.sync_jobs[job.id].status is JobStatus.COMPLETED
    assert twse.calls and not tpex.calls
    assert await repository.list_market_trading_dates("TW", as_of=None, limit=1)


# 2026-07-15 is a Wednesday. 07:30 UTC = 15:30 Taipei; 13:45 UTC = 21:45 Taipei.
AFTERNOON = datetime(2026, 7, 15, 7, 45, tzinfo=UTC)
EVENING = datetime(2026, 7, 15, 13, 50, tzinfo=UTC)


async def test_each_daily_time_triggers_one_refresh_including_market_jobs() -> None:
    repository = MemoryQuantRepository()
    repository.instruments["2330.TW"] = Instrument(symbol="2330.TW", market="TW")
    args = {"daily_time": "15:30,21:45", "lookback_days": 500, "market_lookback_days": 260}

    afternoon = await enqueue_daily_refresh(repository, now=AFTERNOON, **args)
    again = await enqueue_daily_refresh(repository, now=AFTERNOON, **args)

    assert afternoon == 4  # 2 markets + price + flows for the one tracked symbol
    assert again == 0
    markets = [j for j in repository.sync_jobs.values() if j.kind is SyncJobKind.MARKET]
    assert sorted(j.symbol for j in markets) == ["TW", "TWO"]
    assert all(j.lookback_days == 260 for j in markets)

    # Finish the afternoon jobs, then the evening time becomes due on its own.
    for job_id, job in list(repository.sync_jobs.items()):
        repository.sync_jobs[job_id] = job.model_copy(update={"status": JobStatus.COMPLETED})
    evening = await enqueue_daily_refresh(repository, now=EVENING, **args)
    assert evening == 4
    assert await enqueue_daily_refresh(repository, now=EVENING, **args) == 0


# ------------------------------------------------- self-healing of suspicious empty days


class FlakyMarketProvider(FakeMarketProvider):
    """Returns nothing for ``broken`` days although they were trading days."""

    def __init__(self, holidays: set[date] | None = None, broken: set[date] | None = None):
        super().__init__(holidays)
        self.broken = broken or set()

    async def fetch_market_stock_day(self, trading_date: date):
        rows = await super().fetch_market_stock_day(trading_date)
        return [] if trading_date in self.broken else rows


HOLIDAY = date(2026, 10, 12)  # both exchanges closed
BROKEN = date(2026, 10, 13)  # TPEx answered nothing, TWSE traded


def _services(repository, twse, tpex, now):
    tw = MarketSnapshotSyncService(
        repository,
        twse,
        market="TW",
        source="twse_official",
        sibling=("tpex_official", "TWO"),
        clock=lambda: now["value"],
    )
    two = MarketSnapshotSyncService(
        repository,
        tpex,
        market="TWO",
        source="tpex_official",
        sibling=("twse_official", "TW"),
        clock=lambda: now["value"],
    )
    return tw, two


async def test_an_empty_day_the_other_exchange_traded_is_retried_and_stored() -> None:
    repository = MemoryQuantRepository()
    twse = FlakyMarketProvider(holidays={HOLIDAY})
    tpex = FlakyMarketProvider(holidays={HOLIDAY}, broken={BROKEN})
    now = {"value": _at(date(2026, 10, 14))}  # 16:00 Taipei the next day
    tw, two = _services(repository, twse, tpex, now)
    window = {"start": HOLIDAY, "end": BROKEN}

    await tw.sync(**window)
    await two.sync(**window)
    entry = repository.fetch_coverage[("tpex_official", "market_stock_day", "TWO", "2026-10-13")]
    assert entry.status == "empty"

    # Too soon: the retry waits at least as long as the day was old when fetched (16 h).
    tpex.calls.clear()
    now["value"] = _at(date(2026, 10, 14), hour=20)
    assert (await two.sync(**window)).periods_fetched == 0 and tpex.calls == []

    tpex.broken.clear()  # e.g. a fresh process whose index cache is not stale
    now["value"] = _at(date(2026, 10, 15))
    report = await two.sync(**window)

    assert tpex.calls == [BROKEN]  # the shared holiday is final and not asked again
    assert report.market_observations_inserted == 2
    entry = repository.fetch_coverage[("tpex_official", "market_stock_day", "TWO", "2026-10-13")]
    assert entry.status == "ok" and entry.row_count == 2

    tpex.calls.clear()
    now["value"] = _at(date(2026, 10, 30))
    assert (await two.sync(**window)).periods_fetched == 0 and tpex.calls == []


async def test_a_day_that_keeps_coming_back_empty_backs_off() -> None:
    repository = MemoryQuantRepository()
    twse = FlakyMarketProvider()
    tpex = FlakyMarketProvider(broken={BROKEN})
    now = {"value": _at(date(2026, 10, 14))}
    tw, two = _services(repository, twse, tpex, now)
    window = {"start": BROKEN, "end": BROKEN}
    await tw.sync(**window)

    attempts: list[datetime] = []
    moment = _at(date(2026, 10, 14))
    while moment < _at(date(2026, 12, 31)):
        now["value"] = moment
        tpex.calls.clear()
        await two.sync(**window)
        if tpex.calls:
            attempts.append(moment)
        moment += timedelta(hours=1)

    # First fetch, then waits that double with the day's age: a handful, not one per run.
    assert 4 <= len(attempts) <= 8
    gaps = [b - a for a, b in zip(attempts, attempts[1:], strict=False)]
    assert gaps == sorted(gaps) and gaps[0] >= timedelta(hours=6)


async def test_the_sibling_is_optional_and_an_empty_without_it_stays_final() -> None:
    repository = MemoryQuantRepository()
    twse = FlakyMarketProvider()
    tpex = FlakyMarketProvider(broken={BROKEN})
    now = {"value": _at(date(2026, 10, 14))}
    tw, _ = _services(repository, twse, tpex, now)
    await tw.sync(start=BROKEN, end=BROKEN)
    lone = MarketSnapshotSyncService(
        repository, tpex, market="TWO", source="tpex_official", clock=lambda: now["value"]
    )
    await lone.sync(start=BROKEN, end=BROKEN)
    now["value"] = _at(date(2026, 11, 30))
    assert (await lone.sync(start=BROKEN, end=BROKEN)).periods_fetched == 0


async def test_worker_queues_a_repair_job_and_the_job_heals_the_day() -> None:
    repository = MemoryQuantRepository()
    twse = FlakyMarketProvider(holidays={HOLIDAY})
    tpex = FlakyMarketProvider(holidays={HOLIDAY}, broken={BROKEN})
    now = {"value": _at(date(2026, 10, 14))}
    tw, two = _services(repository, twse, tpex, now)
    await tw.sync(start=HOLIDAY, end=BROKEN)
    await two.sync(start=HOLIDAY, end=BROKEN)

    later = _at(date(2026, 10, 17))  # a Saturday: no daily refresh would come
    # Providers without ETF rows: only the stock repair can queue, exactly as before.
    providers = {"TW": twse, "TWO": tpex}
    assert await enqueue_market_repairs(
        repository, now=later, lookback_days=30, providers=providers
    ) == ["TWO"]
    jobs = [j for j in repository.sync_jobs.values() if j.kind is SyncJobKind.MARKET]
    assert [(j.symbol, j.lookback_days) for j in jobs] == [("TWO", 30)]

    tpex.broken.clear()
    tpex.calls.clear()
    outcome = await process_next_job(
        repository,
        twse,
        tpex,
        max_attempts=2,
        today=date(2026, 10, 17),  # type: ignore[arg-type]
    )
    assert outcome is True
    assert BROKEN in tpex.calls and HOLIDAY not in tpex.calls
    entry = repository.fetch_coverage[("tpex_official", "market_stock_day", "TWO", "2026-10-13")]
    assert entry.status == "ok"
    assert await enqueue_market_repairs(repository, now=later, lookback_days=30) == []


async def test_no_repair_job_when_both_exchanges_were_empty() -> None:
    repository = MemoryQuantRepository()
    twse = FlakyMarketProvider(holidays={HOLIDAY})
    tpex = FlakyMarketProvider(holidays={HOLIDAY})
    now = {"value": _at(date(2026, 10, 14))}
    tw, two = _services(repository, twse, tpex, now)
    await tw.sync(start=HOLIDAY, end=BROKEN)
    await two.sync(start=HOLIDAY, end=BROKEN)

    later = _at(date(2026, 10, 20))
    assert await enqueue_market_repairs(repository, now=later, lookback_days=30) == []
    assert repository.sync_jobs == {}
