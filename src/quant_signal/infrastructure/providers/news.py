from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
GOOGLE_NEWS_SOURCE = "google_news_rss"
SEARXNG_SOURCE = "searxng"


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_FEED_ITEMS = 200
USER_AGENT = "Mozilla/5.0 (compatible; quant-signal-news/1.0)"
_HEADERS = {"User-Agent": USER_AGENT}
_ENTITY_DECLARATION = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


class FeedError(ValueError):
    """A feed or search response that must not be parsed (too large, unsafe, malformed)."""


async def read_limited(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    limit: int = MAX_RESPONSE_BYTES,
) -> str:
    """GET ``url`` and return the body text, refusing bodies larger than ``limit`` bytes."""
    async with client.stream(
        "GET", url, params=params, headers={**_HEADERS, **(headers or {})}
    ) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                raise FeedError(f"response from {urlsplit(url).hostname} exceeds {limit} bytes")
        encoding = response.encoding or "utf-8"
    return body.decode(encoding, errors="replace")


def safe_xml_root(xml_text: str) -> ElementTree.Element:
    """Parse untrusted XML; DTDs and entity declarations (entity expansion) are refused."""
    if _ENTITY_DECLARATION.search(xml_text):
        raise FeedError("feed contains a DOCTYPE/ENTITY declaration")
    return ElementTree.fromstring(xml_text)


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


def _parse_feed_time(text: str) -> datetime | None:
    """RFC 822 (RSS ``pubDate``) or ISO 8601 (Atom ``published``/``updated``)."""
    text = text.strip()
    if not text:
        return None
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def parse_feed(xml_text: str, *, source: str, fetched_at: datetime) -> list[RawHeadline]:
    """Parse an RSS 2.0 or Atom document into headlines.

    Items without a title, link or a parseable time are dropped. A publish time in
    the future (a broken feed clock) is clamped to ``fetched_at``.
    """
    root = safe_xml_root(xml_text)
    entries: list[tuple[str, str, str]] = []
    for item in root.findall("./channel/item"):
        entries.append(
            (
                item.findtext("title") or "",
                item.findtext("link") or "",
                item.findtext("pubDate")
                or item.findtext("{http://purl.org/dc/elements/1.1/}date")
                or "",
            )
        )
    atom = "{http://www.w3.org/2005/Atom}"
    for entry in root.findall(f"{atom}entry"):
        link = ""
        for node in entry.findall(f"{atom}link"):
            if node.get("rel", "alternate") == "alternate" and node.get("href"):
                link = node.get("href", "")
                break
        entries.append(
            (
                entry.findtext(f"{atom}title") or "",
                link,
                entry.findtext(f"{atom}published") or entry.findtext(f"{atom}updated") or "",
            )
        )
    headlines: list[RawHeadline] = []
    for title, link, stamp in entries[:MAX_FEED_ITEMS]:
        title, link = title.strip(), link.strip()
        published_at = _parse_feed_time(stamp)
        if not title or not link or published_at is None:
            continue
        headlines.append(
            RawHeadline(
                headline=title,
                url=link,
                source=source,
                published_at=min(published_at, fetched_at),
            )
        )
    return headlines


class RssFeedProvider:
    """Reads whole RSS/Atom feeds from configured URLs (``NEWS_FEED_URLS``).

    Unlike the per-company providers it has no query: each feed's items are matched
    to symbols afterwards (``news_matching.match_symbols``). A self-hosted RSSHub
    route or any publisher feed is just another URL. A feed that fails is logged
    by the caller and skipped; the others still load.
    """

    def __init__(self, urls: list[str], *, client: httpx.AsyncClient | None = None) -> None:
        self.urls = [url.strip() for url in urls if url.strip()]
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=20, follow_redirects=True, headers=_HEADERS
        )

    async def __aenter__(self) -> RssFeedProvider:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owned_client:
            await self.client.aclose()

    @staticmethod
    def source_for(url: str) -> str:
        host = urlsplit(url).hostname or "feed"
        return f"rss:{host}"[:64]

    async def fetch_url(self, url: str) -> list[RawHeadline]:
        text = await read_limited(self.client, url)
        return parse_feed(text, source=self.source_for(url), fetched_at=datetime.now(UTC))


class GoogleNewsRssProvider:
    """Free, keyless Google News RSS search. ``pubDate`` is a genuine
    historical timestamp, so results are safe to use for point-in-time
    backfill, not just current sentiment."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(timeout=20, headers=_HEADERS)

    async def __aenter__(self) -> GoogleNewsRssProvider:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def fetch(self, query: str) -> list[RawHeadline]:
        text = await read_limited(
            self.client,
            GOOGLE_NEWS_RSS_URL,
            params={"q": query, "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"},
        )
        return parse_feed(text, source=GOOGLE_NEWS_SOURCE, fetched_at=datetime.now(UTC))


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
        self.client = client or httpx.AsyncClient(timeout=20, headers=_HEADERS)

    async def __aenter__(self) -> SearxNgNewsProvider:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def fetch(self, query: str) -> list[RawHeadline]:
        text = await read_limited(
            self.client,
            f"{self.base_url}/search",
            params={"q": query, "category": "news"},
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        payload = json.loads(text)
        fetched_at = datetime.now(UTC)
        headlines: list[RawHeadline] = []
        for result in payload.get("results", [])[:MAX_FEED_ITEMS]:
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
