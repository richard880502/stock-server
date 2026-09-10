from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
GOOGLE_NEWS_SOURCE = "google_news_rss"
SEARXNG_SOURCE = "searxng"


@dataclass(frozen=True)
class RawHeadline:
    """An unclassified headline pulled from a fetch provider.

    ``published_at`` is a real historical timestamp for RSS sources and is
    only ever the fetch time for search-engine sources that don't expose one
    (see ``SearxNgNewsProvider``) — callers must not treat the latter as
    point-in-time-safe for backfilling history, only for current sentiment.
    """

    headline: str
    url: str
    source: str
    published_at: datetime


class GoogleNewsRssProvider:
    """Free, keyless Google News RSS search. ``pubDate`` is a genuine
    historical timestamp, so results are safe to use for point-in-time
    backfill, not just current sentiment."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(timeout=20)

    async def __aenter__(self) -> GoogleNewsRssProvider:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def fetch(self, query: str) -> list[RawHeadline]:
        response = await self.client.get(
            GOOGLE_NEWS_RSS_URL,
            params={"q": query, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"},
        )
        response.raise_for_status()
        root = ElementTree.fromstring(response.text)
        headlines: list[RawHeadline] = []
        for item in root.findall("./channel/item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date_text = (item.findtext("pubDate") or "").strip()
            if not title or not link or not pub_date_text:
                continue
            try:
                published_at = parsedate_to_datetime(pub_date_text)
            except (TypeError, ValueError):
                continue
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=UTC)
            headlines.append(
                RawHeadline(
                    headline=title,
                    url=link,
                    source=GOOGLE_NEWS_SOURCE,
                    published_at=published_at.astimezone(UTC),
                )
            )
        return headlines


class SearxNgNewsProvider:
    """Queries a self-hosted SearxNG instance through the authenticated
    ``searxng-proxy`` service. General web/news results usually carry no
    trustworthy historical publish date, so every result is stamped with the
    fetch time — useful for *current* sentiment only, never for backfill."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(timeout=20)

    async def __aenter__(self) -> SearxNgNewsProvider:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def fetch(self, query: str) -> list[RawHeadline]:
        response = await self.client.get(
            f"{self.base_url}/search",
            params={"q": query, "category": "news"},
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        response.raise_for_status()
        payload = response.json()
        fetched_at = datetime.now(UTC)
        headlines: list[RawHeadline] = []
        for result in payload.get("results", []):
            title = str(result.get("title") or "").strip()
            url = str(result.get("url") or "").strip()
            if not title or not url:
                continue
            headlines.append(
                RawHeadline(
                    headline=title,
                    url=url,
                    source=SEARXNG_SOURCE,
                    published_at=fetched_at,
                )
            )
        return headlines
