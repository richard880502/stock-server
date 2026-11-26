"""The scheduled news job: runs inside the ingest service, twice a day by default.

Scope is the union of every user's watchlist and the tracked instruments, capped.
News is only stored for symbols that already have an ``instruments`` row (the
watchlist's background sync creates it); the rest are skipped and counted. One
(symbol, slot) pair is fetched at most once, recorded in ``fetch_coverage``, and a
failure on one symbol never blocks the others.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from quant_signal.application.news_sync import NewsSentimentSyncService, fixed_repository
from quant_signal.application.ports import QuantRepository
from quant_signal.domain.models import FetchCoverage

logger = logging.getLogger(__name__)

TAIPEI = ZoneInfo("Asia/Taipei")
NEWS_SOURCE = "news_sync"
SYMBOL_DATASET = "symbol_news"
FEEDS_DATASET = "feeds"
MAX_ATTEMPTS_PER_SLOT = 3


@dataclass
class NewsRunReport:
    slot: str | None = None
    synced: int = 0
    skipped_no_instrument: int = 0
    failed: int = 0
    already_done: int = 0
    rows: int = 0
    feed_rows: int = 0
    symbols: list[str] = field(default_factory=list)


def latest_due_slot(news_time: str, now: datetime) -> str | None:
    """The most recent ``YYYY-MM-DD@HH:MM`` slot (Taipei) that has already passed today."""
    local = now.astimezone(TAIPEI)
    due: list[str] = []
    for stamp in (part.strip() for part in news_time.split(",") if part.strip()):
        hour, minute = (int(part) for part in stamp.split(":"))
        if (local.hour, local.minute) >= (hour, minute):
            due.append(f"{local.date().isoformat()}@{stamp}")
    return max(due) if due else None


async def news_scope(repository: QuantRepository, *, max_symbols: int) -> list[str]:
    """Watchlist symbols first (what users look at), then tracked instruments; capped."""
    ordered: list[str] = []
    seen: set[str] = set()
    for symbol in [
        *await repository.list_watchlist_symbols(),
        *await repository.list_tracked_symbols(),
    ]:
        upper = symbol.strip().upper()
        if upper and upper not in seen and not upper.startswith("^"):
            seen.add(upper)
            ordered.append(upper)
    return ordered[: max(max_symbols, 0)]


async def run_news_slot(
    repository: Any,
    service: NewsSentimentSyncService,
    *,
    news_time: str,
    max_symbols: int,
    now: datetime,
    attempts: dict[tuple[str, str], int] | None = None,
    delay_seconds: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> NewsRunReport:
    """Run the latest due slot once for every symbol not already done for it.

    ``repository`` is a repository or a factory of short-lived repository scopes (one
    database session per phase; none is open while waiting on the network). A symbol
    whose fetch failed is counted as failed and *not* recorded in ``fetch_coverage``,
    so it is retried (up to ``MAX_ATTEMPTS_PER_SLOT`` times) rather than looking done.
    """
    report = NewsRunReport(slot=latest_due_slot(news_time, now))
    if report.slot is None:
        return report
    slot = report.slot
    open_repo = repository if callable(repository) else fixed_repository(repository)
    attempts = attempts if attempts is not None else {}
    # Only the current slot's counters matter; drop the rest so the dict stays small.
    for key in [key for key in attempts if key[1] != slot]:
        del attempts[key]

    async with open_repo() as repo:
        scope = await news_scope(repo, max_symbols=max_symbols)
        done = {
            symbol
            for symbol in scope
            if slot in await repo.list_fetch_coverage(NEWS_SOURCE, SYMBOL_DATASET, symbol, [slot])
        }
        report.already_done = len(done)
        eligible: list[str] = []
        for symbol in scope:
            if symbol in done:
                continue
            if await repo.instrument_exists(symbol):
                eligible.append(symbol)
            else:
                report.skipped_no_instrument += 1
    if report.skipped_no_instrument and ("*skipped", slot) not in attempts:
        attempts[("*skipped", slot)] = 1  # note it once per slot, not on every poll
        logger.info(
            "news slot %s: %s symbols skipped, no instruments row yet",
            slot,
            report.skipped_no_instrument,
        )
    if eligible:
        await _run_feeds(open_repo, service, eligible, slot, now, report, attempts)

    first = True
    for symbol in eligible:
        if attempts.get((symbol, slot), 0) >= MAX_ATTEMPTS_PER_SLOT:
            continue
        attempts[(symbol, slot)] = attempts.get((symbol, slot), 0) + 1
        if not first:
            await sleep(delay_seconds)
        first = False
        try:
            rows = await service.sync_symbol(symbol)
            async with open_repo() as repo:
                await repo.record_fetch_coverage(
                    [
                        FetchCoverage(
                            source=NEWS_SOURCE,
                            dataset=SYMBOL_DATASET,
                            symbol=symbol,
                            period=slot,
                            status="ok",
                            row_count=rows,
                            fetched_at=now,
                        )
                    ]
                )
        except Exception:
            report.failed += 1
            logger.exception("news sync failed for %s", symbol)
            continue
        report.synced += 1
        report.rows += rows
        report.symbols.append(symbol)
    if report.synced or report.failed:
        logger.info(
            "news slot %s: %s synced (%s rows, %s from feeds), %s failed, %s already done",
            slot,
            report.synced,
            report.rows,
            report.feed_rows,
            report.failed,
            report.already_done,
        )
    return report


async def _run_feeds(
    open_repo: Any,
    service: NewsSentimentSyncService,
    symbols: list[str],
    slot: str,
    now: datetime,
    report: NewsRunReport,
    attempts: dict[tuple[str, str], int],
) -> None:
    if service.feed_provider is None or not service.feed_provider.urls:
        return
    async with open_repo() as repo:
        if slot in await repo.list_fetch_coverage(NEWS_SOURCE, FEEDS_DATASET, "*", [slot]):
            return
    key = ("*feeds", slot)
    if attempts.get(key, 0) >= MAX_ATTEMPTS_PER_SLOT:
        return
    attempts[key] = attempts.get(key, 0) + 1
    try:
        written = await service.sync_feeds(symbols)
    except Exception:
        logger.exception("news feed sync failed")
        return
    report.feed_rows = sum(written.values())
    async with open_repo() as repo:
        await repo.record_fetch_coverage(
            [
                FetchCoverage(
                    source=NEWS_SOURCE,
                    dataset=FEEDS_DATASET,
                    symbol="*",
                    period=slot,
                    status="ok",
                    row_count=report.feed_rows,
                    fetched_at=now,
                )
            ]
        )
