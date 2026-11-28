"""One grouped query per feed: what the screener snapshot's inputs look like per date."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from quant_signal.domain.feed_stats import FeedDayStat
from quant_signal.infrastructure.db.derivatives_models import StockFuturesRow
from quant_signal.infrastructure.db.models import (
    DayTradeDailyRow,
    ForeignHoldingRow,
    ShareholdingDistributionRow,
    ShortLendingDailyRow,
    ValuationDailyRow,
)

# feed name -> (ORM row, date column)
FEED_TABLES: dict[str, tuple[Any, Any]] = {
    "valuation": (ValuationDailyRow, ValuationDailyRow.trading_date),
    "short_lending": (ShortLendingDailyRow, ShortLendingDailyRow.trading_date),
    "day_trade": (DayTradeDailyRow, DayTradeDailyRow.trading_date),
    "foreign_holding": (ForeignHoldingRow, ForeignHoldingRow.trading_date),
    "stock_futures": (StockFuturesRow, StockFuturesRow.trading_date),
    "shareholding": (ShareholdingDistributionRow, ShareholdingDistributionRow.data_date),
}


class FeedStatsRepositoryMixin:
    session: AsyncSession

    async def feed_day_stats(self, start: date, end: date) -> dict[str, list[FeedDayStat]]:
        """Per feed and trading date in ``[start, end]``: row count and the first / last
        ``available_at`` (oldest date first). One indexed, grouped query per feed."""
        out: dict[str, list[FeedDayStat]] = {}
        for name, (row, day_column) in FEED_TABLES.items():
            statement = (
                select(
                    day_column,
                    func.count(),
                    func.min(row.available_at),
                    func.max(row.available_at),
                )
                .where(day_column >= start, day_column <= end)
                .group_by(day_column)
                .order_by(day_column)
            )
            out[name] = [
                FeedDayStat(
                    trading_date=day, rows=rows, first_available_at=first, last_available_at=last
                )
                for day, rows, first, last in (await self.session.execute(statement)).all()
            ]
        return out
