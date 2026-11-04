from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from fakes import FakeMarketProvider
from fastapi.testclient import TestClient

from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.adapters.rest.security import _get_repository
from quant_signal.application.market_backfill import MarketBackfillService
from quant_signal.application.market_sync import MARKET_STOCK_DATASET, MarketSnapshotSyncService
from quant_signal.domain.models import FetchCoverage, JobStatus, SyncJobKind
from quant_signal.infrastructure.memory import MemoryQuantRepository

SOURCE = "twse_official"
# Wednesday 2026-11-04, 14:00 Taipei.
NOW = datetime(2026, 11, 4, 6, 0, tzinfo=UTC)
LOOKBACK = 20


def _weekdays() -> list[date]:
    today = date(2026, 11, 4)
    start = today - timedelta(days=LOOKBACK)
    return [
        start + timedelta(days=i)
        for i in range((today - start).days + 1)
        if (start + timedelta(days=i)).weekday() < 5
    ]


async def _record(repo: MemoryQuantRepository, days: list[date], *, minutes_ago: float) -> None:
    await repo.record_fetch_coverage(
        [
            FetchCoverage(
                source=SOURCE,
                dataset=MARKET_STOCK_DATASET,
                symbol="TW",
                period=day.isoformat(),
                status="ok",
                row_count=1,
                fetched_at=NOW - timedelta(minutes=minutes_ago),
            )
            for day in days
        ]
    )


def _service(repo) -> MarketBackfillService:
    return MarketBackfillService(repo, lookback_days=LOOKBACK, clock=lambda: NOW)


async def test_running_fill_reports_percent_rate_and_eta() -> None:
    repo = MemoryQuantRepository()
    days = _weekdays()
    # Newest-first fill: the 6 newest days are done, 5 of them in the last 10 minutes.
    done = sorted(days, reverse=True)[:6]
    await _record(repo, done[:1], minutes_ago=60 * 30)  # fetched long ago (previous day)
    await _record(repo, done[1:], minutes_ago=2)
    job, _ = await repo.enqueue_sync_job(SyncJobKind.MARKET, "TW", lookback_days=LOOKBACK)
    await repo.claim_next_sync_job()

    tw, two = await _service(repo).status()

    assert tw.state == "running" and tw.days_total == len(days)
    # Days fetched on an earlier calendar day count; same-day ones are still "pending"
    # by the sync's own rule, so progress never claims more than the job believes.
    assert 0 < tw.days_done < len(days) and tw.percent == round(tw.days_done / len(days) * 100, 1)
    assert tw.rate_per_minute == 0.5  # 5 days fetched in the last 10 minutes
    remaining = len(days) - tw.days_done
    assert tw.eta_seconds == int(remaining / 0.5 * 60)
    assert two.state == "idle" and two.days_done == 0  # nothing scheduled for OTC here
    assert job.symbol == "TW"


async def test_queued_job_without_progress_has_no_eta() -> None:
    repo = MemoryQuantRepository()
    await repo.enqueue_sync_job(SyncJobKind.MARKET, "TW", lookback_days=LOOKBACK)

    tw, _ = await _service(repo).status()

    assert tw.state == "queued" and tw.rate_per_minute is None and tw.eta_seconds is None
    assert tw.percent == 0.0


async def test_a_nearly_finished_window_counts_as_complete() -> None:
    repo = MemoryQuantRepository()
    days = _weekdays()
    await _record(repo, days[:-1], minutes_ago=60 * 24 * 2)  # all but today, fetched earlier

    tw, _ = await _service(repo).status()

    # Days fetched on the day itself still count as pending (late reports), so a
    # handful of recent ones remain; that is steady state, not a backfill.
    assert tw.state == "complete" and len(days) - tw.days_done <= 3


async def test_score_readiness_follows_the_latest_observation() -> None:
    from test_market_observations import _Provider, _seeded
    from test_market_observations import _service as observation_service

    repo, sessions = await _seeded(65)
    now = datetime.combine(sessions[-1], time(8), tzinfo=UTC)
    await observation_service(repo, _Provider(), now).sync(end=sessions[-1])

    status = await MarketBackfillService(repo, lookback_days=LOOKBACK, clock=lambda: now).status()
    tw = status[0]

    assert tw.stored_sessions == 65
    assert tw.latest_score_date == sessions[-1] and tw.score_ready
    assert tw.sessions_needed_for_score == 252


async def test_progress_hook_runs_between_batches_not_at_the_end() -> None:
    repo = MemoryQuantRepository()
    provider = FakeMarketProvider()
    calls: list[int] = []

    async def hook() -> None:
        calls.append(len(await repo.list_market_trading_dates("TW", as_of=None, limit=100)))

    service = MarketSnapshotSyncService(
        repo, provider, market="TW", source=SOURCE, clock=lambda: NOW
    )
    await service.sync(
        start=date(2026, 10, 12), end=date(2026, 10, 16), on_progress=hook, progress_every=2
    )

    # 5 weekdays, hook after the 2nd and 4th day stored (newest first), not after the 5th.
    assert calls == [2, 4]


def test_backfill_status_endpoint() -> None:
    repo = MemoryQuantRepository()

    async def override():
        yield repo

    app.dependency_overrides[get_repository] = override
    app.dependency_overrides[_get_repository] = override
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/market/backfill-status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert [item["market"] for item in body] == ["TW", "TWO"]
    assert {"state", "percent", "eta_seconds", "score_ready"} <= set(body[0])


def test_job_status_enum_values_used_by_the_service() -> None:
    assert JobStatus.RUNNING.value == "running"
