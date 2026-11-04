"""Quick stock pages and per-user watchlists, served from the whole-market snapshot.

Nothing here calls an exchange: quotes, flows and credit balances come from
``market_stock_daily``, so any listed stock opens instantly. The adjusted history
that signals and backtests need is queued in the background.
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from quant_signal.application.data_sync import _recently_synced
from quant_signal.application.ports import QuantRepository
from quant_signal.application.sync_jobs import request_symbol_sync
from quant_signal.domain.models import (
    MarketStockDay,
    StockDayPoint,
    StockOverview,
    StockSummary,
    Watchlist,
    WatchlistItem,
)

OVERVIEW_SESSIONS = 360  # about 17 months of trading days
MINIMUM_BARS = 60
LOOKUP_SESSIONS = 20  # a stock halted today is still found by its latest quote


class StockNotFound(LookupError):
    def __init__(self, symbol: str) -> None:
        super().__init__(f"找不到股票 {symbol}（目前僅收錄上市、上櫃股票，不含 ETF）")
        self.symbol = symbol


class WatchlistFull(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"自選股已達上限 {limit} 檔，請先移除一檔再新增")
        self.limit = limit


def _lots(shares: int | None) -> float | None:
    return None if shares is None else round(shares / 1000, 1)


def _pct(new: float, old: float | None) -> float | None:
    if not old:
        return None
    return round((new / old - 1) * 100, 2)


def _points(rows: list[MarketStockDay]) -> list[StockDayPoint]:
    ordered = sorted(rows, key=lambda item: item.trading_date)
    points: list[StockDayPoint] = []
    for row in ordered:
        previous = row.close - row.change
        points.append(
            StockDayPoint(
                trading_date=row.trading_date,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume_lots=row.volume // 1000,
                turnover_value=row.turnover_value,
                change_pct=_pct(row.close, previous) if previous > 0 else None,
                foreign_net_lots=_lots(row.foreign_net),
                trust_net_lots=_lots(row.trust_net),
                dealer_net_lots=_lots(row.dealer_net),
                total_net_lots=_lots(row.total_net),
                margin_balance=row.margin_balance,
                margin_change=row.margin_change,
                short_balance=row.short_balance,
                short_change=row.short_change,
            )
        )
    return points


def _reported(points: list[StockDayPoint], field: str, count: int) -> list[StockDayPoint]:
    """The newest ``count`` sessions whose report for ``field`` is out.

    The newest session usually has quotes hours before the institutional-flow
    report, so a window of "the last 5 days" skips it rather than undercounting.
    """
    return [point for point in points if getattr(point, field) is not None][-count:]


def _sum_flow(points: list[StockDayPoint], field: str, count: int) -> float | None:
    window = _reported(points, field, count)
    return round(sum(getattr(point, field) for point in window), 1) if window else None


def _flows_through(points: list[StockDayPoint]) -> date | None:
    window = _reported(points, "total_net_lots", 1)
    return window[-1].trading_date if window else None


def _summary(points: list[StockDayPoint]) -> StockSummary:
    last = points[-1]

    def back(sessions: int) -> float | None:
        if len(points) <= sessions:
            return None
        return _pct(last.close, points[-1 - sessions].close)

    volumes = [point.volume_lots for point in points[-20:]]
    return StockSummary(
        last_close=last.close,
        day_change_pct=last.change_pct,
        return_5d_pct=back(5),
        return_20d_pct=back(20),
        flows_through=_flows_through(points),
        foreign_net_5d_lots=_sum_flow(points, "foreign_net_lots", 5),
        total_net_5d_lots=_sum_flow(points, "total_net_lots", 5),
        avg_volume_20d_lots=round(sum(volumes) / len(volumes), 1) if volumes else None,
    )


async def normalize_listed_symbol(repository: QuantRepository, raw: str) -> str:
    """Accept ``2330`` or ``2330.TW``; return the listed symbol or raise."""
    symbol = raw.strip().upper()
    if not symbol:
        raise StockNotFound(raw)
    if "." in symbol:
        return symbol
    matches = [
        item.symbol
        for item in await repository.search_market_stocks(symbol, limit=20)
        if item.symbol.split(".")[0] == symbol
    ]
    if not matches:
        raise StockNotFound(symbol)
    return matches[0]


class StockOverviewService:
    def __init__(self, repository: QuantRepository) -> None:
        self.repository = repository

    async def get(self, raw_symbol: str, *, sessions: int = OVERVIEW_SESSIONS) -> StockOverview:
        symbol = await normalize_listed_symbol(self.repository, raw_symbol)
        dates = await self.repository.list_market_trading_dates(
            None, as_of=None, limit=sessions
        )
        rows = await self.repository.list_market_stock_rows([symbol], dates)
        if not rows:
            raise StockNotFound(symbol)
        points = _points(rows)
        latest = max(rows, key=lambda item: item.trading_date)
        return StockOverview(
            symbol=symbol,
            name=latest.name,
            market=latest.market,
            data_through=points[-1].trading_date,
            trading_days=len(points),
            full_analysis=await self._analysis_state(symbol, points[-1].trading_date),
            summary=_summary(points),
            days=points,
        )

    async def _analysis_state(
        self, symbol: str, through: date
    ) -> Literal["ready", "preparing", "insufficient"]:
        bars = await self.repository.list_bars(symbol, end=through)
        if len(bars) >= MINIMUM_BARS:
            return "ready"
        if await _recently_synced(self.repository, symbol):
            return "insufficient"
        await request_symbol_sync(self.repository, symbol)
        return "preparing"


class WatchlistService:
    def __init__(self, repository: QuantRepository, *, limit: int) -> None:
        self.repository = repository
        self.limit = limit

    async def get(self, user_id: UUID) -> Watchlist:
        entries = await self.repository.list_watchlist(user_id)
        if not entries:
            return Watchlist(limit=self.limit, count=0, items=[])
        dates = await self.repository.list_market_trading_dates(None, as_of=None, limit=6)
        rows = await self.repository.list_market_stock_rows([e[0] for e in entries], dates)
        by_symbol: dict[str, list[MarketStockDay]] = {}
        for row in rows:
            by_symbol.setdefault(row.symbol, []).append(row)
        tracked = set(await self.repository.list_tracked_symbols())
        items: list[WatchlistItem] = []
        for symbol, name, market, added_at in entries:
            points = _points(by_symbol.get(symbol, []))
            last = points[-1] if points else None
            items.append(
                WatchlistItem(
                    symbol=symbol,
                    name=name,
                    market=market,  # type: ignore[arg-type]
                    added_at=added_at,
                    data_through=last.trading_date if last else None,
                    close=last.close if last else None,
                    day_change_pct=last.change_pct if last else None,
                    flows_through=_flows_through(points),
                    foreign_net_1d_lots=_sum_flow(points, "foreign_net_lots", 1),
                    total_net_5d_lots=_sum_flow(points, "total_net_lots", 5),
                    full_analysis="ready" if symbol in tracked else "preparing",
                )
            )
        return Watchlist(limit=self.limit, count=len(items), items=items)

    async def add(self, user_id: UUID, raw_symbol: str) -> Literal["added", "exists"]:
        symbol = await normalize_listed_symbol(self.repository, raw_symbol)
        dates = await self.repository.list_market_trading_dates(
            None, as_of=None, limit=LOOKUP_SESSIONS
        )
        rows = await self.repository.list_market_stock_rows([symbol], dates)
        if not rows:
            raise StockNotFound(symbol)
        latest = max(rows, key=lambda item: item.trading_date)
        outcome = await self.repository.add_watchlist_item(
            user_id,
            symbol=symbol,
            name=latest.name,
            market=latest.market,
            limit=self.limit,
        )
        if outcome == "full":
            raise WatchlistFull(self.limit)
        if outcome == "added":
            await request_symbol_sync(self.repository, symbol)
        return outcome

    async def remove(self, user_id: UUID, raw_symbol: str) -> bool:
        symbol = raw_symbol.strip().upper()
        if "." not in symbol:
            # A bare code resolves against the user's own list, so a stock that has
            # since dropped out of the snapshot can still be removed.
            for owned, *_ in await self.repository.list_watchlist(user_id):
                if owned.split(".")[0] == symbol:
                    symbol = owned
                    break
        return await self.repository.remove_watchlist_item(user_id, symbol)
