from __future__ import annotations

from datetime import date

from quant_signal.domain.models import NewsHeadlineRanking, NewsItem, NewsSentimentSnapshot


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


class NewsSentimentEngine:
    """Aggregates already-classified, already-persisted news items.

    Deliberately pure arithmetic — the LLM classification happens upstream
    in ``application/news_sync.py``, not here, so this module stays free of
    any LLM dependency like every other engine in ``quant_signal.quant``.
    """

    minimum_articles = 1
    max_headlines = 10

    def analyze(
        self,
        items: list[NewsItem],
        *,
        as_of: date,
        strategy_version: str = "news_sentiment_v1",
    ) -> NewsSentimentSnapshot:
        if not items:
            raise ValueError("no point-in-time news items available")
        scored = [item for item in items if item.sentiment_score is not None]
        if len(scored) < self.minimum_articles:
            raise ValueError("no classified news items available")

        average = sum(item.sentiment_score for item in scored) / len(scored)
        score = _clamp((average + 1) * 50)
        confidence = min(len(scored) / 10, 1.0)
        if average > 0.15:
            label = "bullish"
        elif average < -0.15:
            label = "bearish"
        else:
            label = "neutral"

        ranked = sorted(scored, key=lambda item: item.published_at, reverse=True)
        top_headlines = [
            NewsHeadlineRanking(
                headline=item.headline,
                url=item.url,
                source=item.source,
                published_at=item.published_at,
                sentiment_score=item.sentiment_score,
                sentiment_label=item.sentiment_label,
            )
            for item in ranked[: self.max_headlines]
        ]

        return NewsSentimentSnapshot(
            symbol=items[0].symbol,
            as_of=as_of,
            strategy_version=strategy_version,
            score=score,
            confidence=confidence,
            article_count=len(scored),
            sentiment_label=label,
            top_headlines=top_headlines,
            data_available_at=max(item.retrieved_at for item in items),
        )
