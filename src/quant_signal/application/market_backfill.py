"""Progress of the whole-market history fill, derived from the fetch ledger."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from quant_signal.application.data_sync import TAIPEI, _fetched_after
from quant_signal.application.market_sync import MARKET_STOCK_DATASET
from quant_signal.application.ports import QuantRepository
from quant_signal.domain.models import MarketBackfillStatus, SyncJobKind
from quant_signal.infrastructure.providers.tpex import TPEX_SOURCE
from quant_signal.infrastructure.providers.twse import TWSE_SOURCE

RATE_WINDOW = timedelta(minutes=10)
SESSIONS_NEEDED = 252
# A couple of days are always "pending" in steady state (today, and yesterday until
# its late reports are fetched), which is not a backfill.
STEADY_STATE_PENDING = 3


class MarketBackfillService:
    def __init__(
        self,
        repository: QuantRepository,
        *,
        lookback_days: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.lookback_days = lookback_days
        self._clock = clock or (lambda: datetime.now(UTC))

    async def status(self) -> list[MarketBackfillStatus]:
        return [await self._one("TW"), await self._one("TWO")]

    async def _one(self, market: Literal["TW", "TWO"]) -> MarketBackfillStatus:
        now = self._clock()
        today = now.astimezone(TAIPEI).date()
        start = today - timedelta(days=self.lookback_days)
        weekdays = [
            start + timedelta(days=offset)
            for offset in range((today - start).days + 1)
            if (start + timedelta(days=offset)).weekday() < 5
        ]
        source = TPEX_SOURCE if market == "TWO" else TWSE_SOURCE
        coverage = await self.repository.list_fetch_coverage(
            source, MARKET_STOCK_DATASET, market, [day.isoformat() for day in weekdays]
        )
        pending = [
            day
            for day in weekdays
            if not _fetched_after(coverage.get(day.isoformat()), day)
        ]
        recent = sum(
            1
            for entry in coverage.values()
            if now - entry.fetched_at <= RATE_WINDOW
        )
        jobs = [
            job
            for job in await self.repository.list_active_sync_jobs(SyncJobKind.MARKET)
            if job.symbol == market
        ]
        running = any(job.status.value == "running" for job in jobs)
        rate = recent / (RATE_WINDOW.total_seconds() / 60) if running and recent else None
        eta = int(len(pending) / rate * 60) if rate and pending else None

        if jobs and len(pending) > STEADY_STATE_PENDING:
            state: Literal["queued", "running", "complete", "idle"] = (
                "running" if running else "queued"
            )
        elif len(pending) <= STEADY_STATE_PENDING:
            state = "complete"
        else:
            state = "idle"  # work remains but nothing is scheduled to do it

        dates = await self.repository.list_market_trading_dates(market, as_of=None, limit=1000)
        observations = [
            item
            for item in await self.repository.list_market_observations(market)
            if item.source == source
        ]
        latest_score = max((item.trading_date for item in observations), default=None)
        newest_stored = dates[0] if dates else None
        score_ready = bool(
            latest_score
            and newest_stored
            and (newest_stored - latest_score).days <= 4
        )
        done = len(weekdays) - len(pending)
        return MarketBackfillStatus(
            market=market,
            state=state,
            lookback_days=self.lookback_days,
            days_total=len(weekdays),
            days_done=done,
            percent=round(done / len(weekdays) * 100, 1) if weekdays else 100.0,
            rate_per_minute=round(rate, 1) if rate else None,
            eta_seconds=eta,
            stored_sessions=len(dates),
            sessions_needed_for_score=SESSIONS_NEEDED,
            latest_score_date=latest_score,
            score_ready=score_ready,
        )

