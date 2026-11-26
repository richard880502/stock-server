"""Sector (industry) rotation over the whole-market daily table.

Everything is computed from ``market_stock_daily`` as it was known at the as-of
date: rows whose ``available_at`` is later than the cut-off are ignored. During
the session the latest intraday snapshot (``market_intraday_snapshots``) stands in
for today's not-yet-published row. Money flow is approximated by turnover share,
not by real fund flows, and the "weighted" return is turnover-weighted because
market capitalisation is not stored.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Literal

from quant_signal.application.data_sync import TAIPEI
from quant_signal.application.ports import QuantRepository
from quant_signal.domain.models import (
    MarketStockDay,
    SectorQuadrant,
    SectorReturn,
    SectorRotation,
    SectorRow,
    SectorRrg,
    SectorRrgPoint,
    SectorStockEntry,
    SectorStocks,
    SyncJobKind,
)

SHARES_PER_LOT = 1000
WINDOWS = (1, 5, 20)
HISTORY_SESSIONS = 30
RS_SMOOTHING = 10
RS_MOMENTUM_LAG = 5
TRAIL_POINTS = 5
SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(13, 30)
# Intraday data must cover at least this share of yesterday's universe to be used.
MIN_INTRADAY_COVERAGE = 0.5
INTRADAY_SOURCE = "intraday_snapshot"

SortKey = Literal["window_return", "day_change", "turnover", "institutional"]


@dataclass
class _Loaded:
    sessions: list[date]
    # symbol -> {session -> row}
    by_symbol: dict[str, dict[date, MarketStockDay]]
    industry: dict[str, tuple[str, str]]
    mode: Literal["close", "intraday"]
    intraday_status: Literal["live", "unavailable", "not_applicable"]
    data_time: datetime | None
    classification_ready: bool
    daily_returns: dict[str, dict[date, float]] = field(default_factory=dict)


def _daily_return(row: MarketStockDay) -> float | None:
    previous = row.close - row.change
    return row.change / previous if previous > 0 else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _quadrant(ratio: float, momentum: float) -> SectorQuadrant:
    if ratio >= 100:
        return "leading" if momentum >= 100 else "weakening"
    return "improving" if momentum >= 100 else "lagging"


def _chain(returns: list[float]) -> list[float]:
    level = 1.0
    out: list[float] = []
    for value in returns:
        level *= 1 + value
        out.append(level)
    return out


def rrg_points(industry: list[float], market: list[float]) -> list[SectorRrgPoint]:
    """RS ratio / momentum (both centred on 100) per session, oldest first.

    Relative strength is the sector's chain-linked equal-weight index over the
    market's; RS ratio is that against its own 10-session mean; momentum is the
    RS ratio against its value 5 sessions earlier. Only fully defined points return.
    """
    if len(industry) != len(market):
        raise ValueError("series must be aligned")
    relative = [a / b for a, b in zip(_chain(industry), _chain(market), strict=True)]
    ratios: list[float | None] = []
    for index in range(len(relative)):
        if index + 1 < RS_SMOOTHING:
            ratios.append(None)
            continue
        window = relative[index + 1 - RS_SMOOTHING : index + 1]
        ratios.append(100 * relative[index] / (sum(window) / RS_SMOOTHING))
    points: list[SectorRrgPoint] = []
    for index, ratio in enumerate(ratios):
        earlier = ratios[index - RS_MOMENTUM_LAG] if index >= RS_MOMENTUM_LAG else None
        if ratio is None or earlier is None:
            continue
        points.append(SectorRrgPoint(rs_ratio=ratio, rs_momentum=100 * ratio / earlier))
    return points


class SectorRotationService:
    def __init__(
        self,
        repository: QuantRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ loading
    async def _load(self, as_of: date | None) -> _Loaded | None:
        now = self._clock()
        local_now = now.astimezone(TAIPEI)
        today = local_now.date()
        present = as_of is None or as_of >= today
        cutoff_date = None if present else as_of
        dates = await self.repository.list_market_trading_dates(
            None, as_of=cutoff_date, limit=HISTORY_SESSIONS + 2
        )
        rows = await self.repository.list_market_stock_days(dates, None, as_of=cutoff_date)
        if present:
            # Point in time: a row is unknown until the moment it became available.
            rows = [row for row in rows if row.available_at <= now]
        industries = {
            item.symbol: (item.industry_code, item.industry_name)
            for item in await self.repository.list_market_industries()
        }
        sessions = sorted({row.trading_date for row in rows})[-HISTORY_SESSIONS:]
        if not sessions:
            return None
        by_symbol: dict[str, dict[date, MarketStockDay]] = defaultdict(dict)
        for row in rows:
            if row.trading_date in sessions:
                by_symbol[row.symbol][row.trading_date] = row

        mode: Literal["close", "intraday"] = "close"
        status: Literal["live", "unavailable", "not_applicable"] = "not_applicable"
        data_time: datetime | None = max(
            (row.available_at for row in rows if row.trading_date == sessions[-1]),
            default=None,
        )
        weekday = local_now.weekday() < 5
        in_session = weekday and SESSION_OPEN <= local_now.time() < SESSION_CLOSE
        if present and weekday and sessions[-1] < today:
            snapshots = await self.repository.latest_intraday_snapshots(day=today, now=now)
            latest_rows = {symbol: days[max(days)] for symbol, days in by_symbol.items()}
            live = [item for item in snapshots if item.symbol in latest_rows]
            universe = sum(1 for days in by_symbol.values() if sessions[-1] in days)
            if live and universe and len(live) / universe >= MIN_INTRADAY_COVERAGE:
                for item in live:
                    base = latest_rows[item.symbol]
                    previous = item.price / (1 + item.change_pct) if item.change_pct > -1 else 0
                    if previous <= 0:
                        continue
                    by_symbol[item.symbol][today] = MarketStockDay(
                        symbol=item.symbol,
                        name=base.name,
                        market=base.market,
                        trading_date=today,
                        open=item.price,
                        high=item.price,
                        low=item.price,
                        close=item.price,
                        volume=item.volume_lots * SHARES_PER_LOT,
                        turnover_value=item.amount_twd,
                        change=item.price - previous,
                        available_at=item.available_at,
                        source=INTRADAY_SOURCE,
                    )
                sessions = [*sessions, today][-HISTORY_SESSIONS:]
                mode, status = "intraday", "live"
                data_time = max(item.taken_at for item in live)
            elif in_session:
                status = "unavailable"
        elif present and in_session:
            status = "unavailable"

        returns: dict[str, dict[date, float]] = {}
        for symbol, days in by_symbol.items():
            returns[symbol] = {
                day: value
                for day, row in days.items()
                if (value := _daily_return(row)) is not None
            }
        return _Loaded(
            sessions=sessions,
            by_symbol=by_symbol,
            industry=industries,
            mode=mode,
            intraday_status=status,
            data_time=data_time,
            classification_ready=bool(industries),
            daily_returns=returns,
        )

    # --------------------------------------------------------------- statistics
    @staticmethod
    def _window_return(
        returns: dict[date, float], sessions: list[date], window: int
    ) -> float | None:
        if len(sessions) < window or sessions[-1] not in returns:
            return None
        level = 1.0
        for day in sessions[-window:]:
            level *= 1 + returns.get(day, 0.0)
        return (level - 1) * 100

    @staticmethod
    def _institutional(
        days: dict[date, MarketStockDay], sessions: list[date]
    ) -> tuple[float, float] | None:
        lots = value = 0.0
        seen = False
        for day in sessions:
            row = days.get(day)
            if row is not None and row.total_net is not None:
                seen = True
                lots += row.total_net / SHARES_PER_LOT
                value += row.total_net * row.close
        return (lots, value) if seen else None

    @staticmethod
    def _members(loaded: _Loaded) -> dict[str, list[str]]:
        latest = loaded.sessions[-1]
        groups: dict[str, list[str]] = defaultdict(list)
        for symbol, days in loaded.by_symbol.items():
            if latest in days and symbol in loaded.industry:
                groups[loaded.industry[symbol][0]].append(symbol)
        return groups

    @staticmethod
    def _daily_series(loaded: _Loaded, symbols: list[str] | None) -> list[float]:
        """Equal-weight return per session over ``symbols`` (None: every stock)."""
        wanted = symbols if symbols is not None else list(loaded.daily_returns)
        series: list[float] = []
        for day in loaded.sessions:
            values = [
                loaded.daily_returns[s][day] for s in wanted if day in loaded.daily_returns[s]
            ]
            series.append(_mean(values) or 0.0)
        return series

    # ------------------------------------------------------------------- public
    async def rotation(
        self, *, window: int = 5, as_of: date | None = None
    ) -> SectorRotation:
        if window not in WINDOWS:
            raise ValueError(f"window must be one of {WINDOWS}")
        loaded = await self._load(as_of)
        pending = await self.repository.has_active_sync_job(SyncJobKind.MARKET)
        if loaded is None:
            return SectorRotation(
                window=window,
                as_of=as_of,
                mode="close",
                intraday_status="not_applicable",
                data_through=None,
                data_time=None,
                trading_days=0,
                institutional_through=None,
                unclassified_count=0,
                classification_ready=False,
                market_turnover_value=0.0,
                market_return_equal_pct={str(w): None for w in WINDOWS},
                backfill_pending=pending,
                sectors=[],
            )
        sessions = loaded.sessions
        window_days = sessions[-window:]
        latest = sessions[-1]
        market_daily_turnover = {
            day: sum(
                days[day].turnover_value for days in loaded.by_symbol.values() if day in days
            )
            for day in sessions
        }
        market_turnover = sum(market_daily_turnover[d] for d in window_days)
        market_series = self._daily_series(loaded, None)
        market_returns: dict[str, float | None] = {
            str(w): (
                (_chain(market_series[-w:])[-1] - 1) * 100 if len(sessions) >= w else None
            )
            for w in WINDOWS
        }
        institutional_through = max(
            (
                day
                for days in loaded.by_symbol.values()
                for day, row in days.items()
                if row.total_net is not None
            ),
            default=None,
        )
        members = self._members(loaded)
        latest_symbols = [s for s, days in loaded.by_symbol.items() if latest in days]
        unclassified = sum(1 for s in latest_symbols if s not in loaded.industry)

        sectors: list[SectorRow] = []
        for code, symbols in members.items():
            name = loaded.industry[symbols[0]][1]
            returns: dict[str, SectorReturn] = {}
            for w in WINDOWS:
                stock_returns: list[tuple[float, float]] = []
                for symbol in symbols:
                    value = self._window_return(loaded.daily_returns[symbol], sessions, w)
                    if value is None:
                        continue
                    weight = _mean(
                        [
                            loaded.by_symbol[symbol][d].turnover_value
                            for d in sessions[-w:]
                            if d in loaded.by_symbol[symbol]
                        ]
                    )
                    stock_returns.append((value, weight or 0.0))
                total_weight = sum(weight for _, weight in stock_returns)
                returns[str(w)] = SectorReturn(
                    equal_pct=_mean([value for value, _ in stock_returns]),
                    weighted_pct=(
                        sum(value * weight for value, weight in stock_returns) / total_weight
                        if total_weight > 0
                        else None
                    ),
                )
            turnover_by_day = {
                day: sum(
                    loaded.by_symbol[s][day].turnover_value
                    for s in symbols
                    if day in loaded.by_symbol[s]
                )
                for day in sessions
            }
            turnover = sum(turnover_by_day[d] for d in window_days)
            share = turnover / market_turnover if market_turnover > 0 else 0.0
            daily_shares = [
                turnover_by_day[d] / market_daily_turnover[d]
                for d in sessions[-20:]
                if market_daily_turnover[d] > 0
            ]
            avg20 = _mean(daily_shares) if len(daily_shares) >= 10 else None
            window_returns = [
                value
                for s in symbols
                if (value := self._window_return(loaded.daily_returns[s], sessions, window))
                is not None
            ]
            up = sum(1 for v in window_returns if v > 0)
            down = sum(1 for v in window_returns if v < 0)
            flat = len(window_returns) - up - down
            flows = [
                flow
                for s in symbols
                if (flow := self._institutional(loaded.by_symbol[s], window_days)) is not None
            ]
            points = rrg_points(self._daily_series(loaded, symbols), market_series)
            rrg = (
                SectorRrg(
                    rs_ratio=points[-1].rs_ratio,
                    rs_momentum=points[-1].rs_momentum,
                    quadrant=_quadrant(points[-1].rs_ratio, points[-1].rs_momentum),
                    trail=points[-TRAIL_POINTS:],
                )
                if points
                else None
            )
            sectors.append(
                SectorRow(
                    code=code,
                    name=name,
                    stock_count=len(symbols),
                    returns=returns,
                    turnover_value=turnover,
                    turnover_share=share,
                    turnover_share_avg20=avg20,
                    turnover_share_change=share - avg20 if avg20 is not None else None,
                    up_count=up,
                    down_count=down,
                    flat_count=flat,
                    breadth_up_pct=up / len(window_returns) * 100 if window_returns else None,
                    institutional_net_lots=sum(f[0] for f in flows) if flows else None,
                    institutional_net_value=sum(f[1] for f in flows) if flows else None,
                    rrg=rrg,
                )
            )
        sectors.sort(key=lambda row: row.turnover_share, reverse=True)
        return SectorRotation(
            window=window,
            as_of=as_of,
            mode=loaded.mode,
            intraday_status=loaded.intraday_status,
            data_through=latest,
            data_time=loaded.data_time,
            trading_days=len(sessions),
            institutional_through=institutional_through,
            unclassified_count=unclassified,
            classification_ready=loaded.classification_ready,
            market_turnover_value=market_turnover,
            market_return_equal_pct=market_returns,
            backfill_pending=pending,
            sectors=sectors,
        )

    async def stocks(
        self,
        code: str,
        *,
        window: int = 5,
        as_of: date | None = None,
        sort: SortKey = "window_return",
        order: Literal["desc", "asc"] = "desc",
        limit: int = 30,
    ) -> SectorStocks:
        if window not in WINDOWS:
            raise ValueError(f"window must be one of {WINDOWS}")
        loaded = await self._load(as_of)
        if loaded is None:
            return SectorStocks(
                code=code,
                name="",
                window=window,
                mode="close",
                data_through=None,
                data_time=None,
                entries=[],
            )
        sessions = loaded.sessions
        window_days = sessions[-window:]
        latest = sessions[-1]
        entries: list[tuple[float | None, SectorStockEntry]] = []
        name = ""
        for symbol in self._members(loaded).get(code, []):
            days = loaded.by_symbol[symbol]
            row = days[latest]
            name = loaded.industry[symbol][1]
            window_return = self._window_return(loaded.daily_returns[symbol], sessions, window)
            day_change = loaded.daily_returns[symbol].get(latest)
            flow = self._institutional(days, window_days)
            entry = SectorStockEntry(
                symbol=symbol,
                name=row.name,
                market=row.market,
                close=row.close,
                day_change_pct=day_change * 100 if day_change is not None else None,
                window_return_pct=window_return,
                volume_lots=row.volume // SHARES_PER_LOT,
                turnover_value=row.turnover_value,
                institutional_net_lots=flow[0] if flow else None,
            )
            key = {
                "window_return": entry.window_return_pct,
                "day_change": entry.day_change_pct,
                "turnover": entry.turnover_value,
                "institutional": entry.institutional_net_lots,
            }[sort]
            entries.append((key, entry))
        known = [item for item in entries if item[0] is not None]
        known.sort(key=lambda item: item[0] or 0.0, reverse=order == "desc")
        return SectorStocks(
            code=code,
            name=name,
            window=window,
            mode=loaded.mode,
            data_through=latest,
            data_time=loaded.data_time,
            entries=[entry for _, entry in known[:limit]],
        )
