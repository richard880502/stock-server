from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal, Protocol

from quant_signal.application.ports import QuantRepository
from quant_signal.application.services import InstrumentSearchService
from quant_signal.domain.models import NewsItem
from quant_signal.infrastructure.providers.news import (
    GoogleNewsRssProvider,
    RawHeadline,
    SearxNgNewsProvider,
)

logger = logging.getLogger(__name__)

NEWS_CLASSIFICATION_PROMPT = """你是一位新聞情緒分類員。你只會收到一則新聞標題與它所屬的股票代號。

規則：
1. 只能根據輸入的標題文字本身判斷情緒，不得補造標題以外的公司背景、財報或即時資訊。
2. 情緒判斷必須保守；標題含糊或與個股無明顯關聯時，判為 neutral、分數接近 0。
3. 使用繁體中文。
4. 只輸出符合下列結構的 JSON，不要 Markdown：
{
  "sentiment_label": "bullish|neutral|bearish",
  "sentiment_score": -1.0,
  "reason": "一句話理由"
}
"""


class LLMClassifier(Protocol):
    async def complete_json(self, system_prompt: str, payload: dict) -> dict: ...


class WritableNewsRepository(QuantRepository, Protocol):
    async def upsert_news_items(self, items: list[NewsItem]) -> int: ...


class NewsSentimentSyncService:
    """Fetches raw headlines, classifies each one, and persists them.

    Kept out of ``quant_signal.quant`` deliberately: the LLM call lives here,
    in the application layer, the same place ``llm_analysis.py`` already
    calls the model — ``quant/news_sentiment.py`` only ever aggregates
    already-classified, already-persisted numbers.
    """

    def __init__(
        self,
        repository: WritableNewsRepository,
        llm_client: LLMClassifier,
        *,
        rss_provider: GoogleNewsRssProvider | None = None,
        searxng_provider: SearxNgNewsProvider | None = None,
    ) -> None:
        self.repository = repository
        self.llm_client = llm_client
        self.rss_provider = rss_provider or GoogleNewsRssProvider()
        self.searxng_provider = searxng_provider

    async def sync_symbol(self, symbol: str, *, limit_per_source: int = 20) -> int:
        normalized = symbol.strip().upper()
        query = await self._resolve_query(normalized)

        raw = await self._fetch_all(query)
        existing = await self.repository.list_news_items(normalized)
        existing_keys = {(item.url, item.source) for item in existing}
        new_raw = [
            headline for headline in raw if (headline.url, headline.source) not in existing_keys
        ][:limit_per_source]

        if not new_raw:
            return 0

        retrieved_at = datetime.now(UTC)
        classified: list[NewsItem] = []
        for headline in new_raw:
            label, score = await self._classify(normalized, headline.headline)
            classified.append(
                NewsItem(
                    symbol=normalized,
                    headline=headline.headline,
                    url=headline.url,
                    source=headline.source,
                    published_at=headline.published_at,
                    sentiment_score=score,
                    sentiment_label=label,
                    retrieved_at=retrieved_at,
                )
            )
        return await self.repository.upsert_news_items(classified)

    async def _resolve_query(self, symbol: str) -> str:
        matches = await InstrumentSearchService(self.repository).search(symbol, limit=1)
        if matches and matches[0].name:
            return matches[0].name
        return symbol

    async def _fetch_all(self, query: str) -> list[RawHeadline]:
        raw: list[RawHeadline] = []
        async with self.rss_provider as rss:
            try:
                raw.extend(await rss.fetch(query))
            except Exception:
                logger.exception("google news rss fetch failed for %s", query)
        if self.searxng_provider is not None:
            async with self.searxng_provider as searxng:
                try:
                    raw.extend(await searxng.fetch(query))
                except Exception:
                    logger.exception("searxng fetch failed for %s", query)
        return raw

    async def _classify(
        self,
        symbol: str,
        headline: str,
    ) -> tuple[Literal["bullish", "neutral", "bearish"], float]:
        try:
            result = await self.llm_client.complete_json(
                NEWS_CLASSIFICATION_PROMPT,
                {"symbol": symbol, "headline": headline},
            )
            label = result.get("sentiment_label")
            score = float(result.get("sentiment_score", 0.0))
            if label not in {"bullish", "neutral", "bearish"}:
                raise ValueError(f"unexpected sentiment_label: {label!r}")
            return label, max(-1.0, min(1.0, score))
        except Exception:
            logger.exception("sentiment classification failed for %s: %s", symbol, headline)
            return "neutral", 0.0
