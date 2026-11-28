"""In-memory twin of ``db/feed_stats_repository.py``."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from quant_signal.domain.feed_stats import FEEDS, FeedDayStat


class MemoryFeedStatsMixin:
    def _feed_rows(self) -> dict[str, tuple[Iterable[Any], str]]:
        sd = getattr(self, "sd_store", {})
        ssf = getattr(self, "_derivatives", {}).get("ssf", {})
        return {
            "valuation": (getattr(self, "valuation_rows", {}).values(), "trading_date"),
            "short_lending": (sd.get("sbl", []), "trading_date"),
            "day_trade": (sd.get("daytrade", []), "trading_date"),
            "foreign_holding": (getattr(self, "foreign_rows", {}).values(), "trading_date"),
            "stock_futures": (ssf.values(), "trading_date"),
            "shareholding": (getattr(self, "shareholding_rows", {}).values(), "data_date"),
        }

    async def feed_day_stats(self, start: date, end: date) -> dict[str, list[FeedDayStat]]:
        sources = self._feed_rows()
        out: dict[str, list[FeedDayStat]] = {}
        for name in FEEDS:
            rows, column = sources[name]
            grouped: dict[date, list[Any]] = {}
            for row in rows:
                day = getattr(row, column)
                if start <= day <= end:
                    grouped.setdefault(day, []).append(row)
            out[name] = [
                FeedDayStat(
                    trading_date=day,
                    rows=len(items),
                    first_available_at=min(i.available_at for i in items),
                    last_available_at=max(i.available_at for i in items),
                )
                for day, items in sorted(grouped.items())
            ]
        return out
