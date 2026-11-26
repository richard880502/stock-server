"""News (消息面) job loop, run as its own task inside the ingest service.

Polls once a minute; ``run_news_slot`` does the work only when a configured Taipei
time has passed and some symbol is not yet done for that slot. Any failure is
logged and retried later; it never touches the price sync tasks.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from quant_signal.application.llm_analysis import _anthropic_client, create_llm_client
from quant_signal.application.news_job import latest_due_slot, run_news_slot
from quant_signal.application.news_sync import NewsSentimentSyncService
from quant_signal.infrastructure.db import PostgresQuantRepository, session_factory
from quant_signal.infrastructure.providers.news import RssFeedProvider, SearxNgNewsProvider
from quant_signal.settings import Settings, get_settings

logger = logging.getLogger(__name__)
POLL_SECONDS = 60


@asynccontextmanager
async def repository_scope() -> AsyncIterator[PostgresQuantRepository]:
    """One short database session; closed (and rolled back if unfinished) on exit."""
    async with session_factory() as session:
        yield PostgresQuantRepository(session)


def build_news_service(settings: Settings, repository=None) -> NewsSentimentSyncService:
    """Sync service wired from settings; no LLM client when the LLM is disabled.

    With no ``repository`` every database phase opens its own session. Use it as
    ``async with build_news_service(...) as service`` so HTTP clients close once.
    """
    if settings.llm_errors():
        llm_client = None
    elif settings.llm_provider == "anthropic" and settings.news_anthropic_model:
        llm_client = _anthropic_client(settings, settings.news_anthropic_model)
    else:
        llm_client = create_llm_client(settings)
    searxng = None
    if settings.searxng_proxy_url and settings.searxng_proxy_key.get_secret_value():
        searxng = SearxNgNewsProvider(
            base_url=settings.searxng_proxy_url,
            api_key=settings.searxng_proxy_key.get_secret_value(),
        )
    urls = [url for url in settings.news_feed_urls.split(",") if url.strip()]
    return NewsSentimentSyncService(
        repository,
        llm_client,
        repository_factory=None if repository is not None else repository_scope,
        searxng_provider=searxng,
        feed_provider=RssFeedProvider(urls) if urls else None,
    )


async def tick(attempts: dict[tuple[str, str], int]) -> None:
    settings = get_settings()
    if latest_due_slot(settings.news_sync_time, datetime.now(UTC)) is None:
        return  # nothing due yet today: do not even build HTTP clients
    # One scope per run: HTTP clients live for the whole tick and are closed at its end.
    async with build_news_service(settings) as service:
        await run_news_slot(
            repository_scope,
            service,
            news_time=settings.news_sync_time,
            max_symbols=settings.news_sync_max_symbols,
            now=datetime.now(UTC),
            attempts=attempts,
            delay_seconds=settings.news_sync_delay_seconds,
        )


async def run_forever() -> None:
    attempts: dict[tuple[str, str], int] = {}
    while True:
        try:
            await tick(attempts)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Usually migrations not applied yet; try again next poll.
            logger.exception("news job tick failed")
        await asyncio.sleep(POLL_SECONDS)
