"""Per-date bookkeeping of the official feeds a screener snapshot reads."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class FeedDayStat(BaseModel):
    """Rows stored for one trading date of one feed: how many, and when they became visible."""

    model_config = ConfigDict(frozen=True)

    trading_date: date
    rows: int
    first_available_at: datetime
    last_available_at: datetime


FEEDS = (
    "valuation",
    "short_lending",
    "day_trade",
    "foreign_holding",
    "stock_futures",
    "shareholding",
)
