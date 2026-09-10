from __future__ import annotations

from datetime import UTC, datetime

from quant_signal.application.news_sync import NewsSentimentSyncService
from quant_signal.domain.models import Instrument
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.infrastructure.providers.news import RawHeadline


class FakeHeadlineProvider:
    def __init__(self, headlines: list[RawHeadline]) -> None:
        self._headlines = headlines

    async def __aenter__(self) -> FakeHeadlineProvider:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def fetch(self, query: str) -> list[RawHeadline]:
        assert query == "台積電"
        return self._headlines


class FakeClassifierClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete_json(self, system_prompt: str, payload: dict) -> dict:
        assert "不得補造" in system_prompt
        headline = payload["headline"]
        self.calls.append(headline)
        if "創新高" in headline:
            return {"sentiment_label": "bullish", "sentiment_score": 0.8, "reason": "利多"}
        return {"sentiment_label": "bearish", "sentiment_score": -0.6, "reason": "利空"}


async def test_news_sentiment_sync_classifies_and_persists_new_headlines() -> None:
    repository = MemoryQuantRepository()
    repository.instruments["2330.TW"] = Instrument(
        symbol="2330.TW", name="台積電", market="TW"
    )
    published_at = datetime(2026, 7, 30, tzinfo=UTC)
    headlines = [
        RawHeadline(
            headline="台積電股價創新高",
            url="https://example.test/a",
            source="google_news_rss",
            published_at=published_at,
        ),
        RawHeadline(
            headline="台積電遭外資調降評等",
            url="https://example.test/b",
            source="google_news_rss",
            published_at=published_at,
        ),
    ]
    classifier = FakeClassifierClient()
    service = NewsSentimentSyncService(
        repository,
        classifier,
        rss_provider=FakeHeadlineProvider(headlines),
    )

    inserted = await service.sync_symbol("2330.TW")

    assert inserted == 2
    assert len(classifier.calls) == 2
    stored = await repository.list_news_items("2330.TW")
    assert {item.sentiment_label for item in stored} == {"bullish", "bearish"}

    # Re-running the sync should skip already-persisted URLs.
    classifier.calls.clear()
    inserted_again = await service.sync_symbol("2330.TW")
    assert inserted_again == 0
    assert classifier.calls == []
