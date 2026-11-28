"""Backend-independent read logic for the short-lending / day-trade / pledge / dividend data.

``ShortDataReadsMixin`` is shared by the Postgres and in-memory repositories; each
implements the four ``sd_*`` primitives (newest revision visible at a cutoff) and the
upserts, everything else is derived here so both backends agree.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from quant_signal.domain.short_data import (
    DayTradeDay,
    DayTradeMetrics,
    DividendEvent,
    InsiderHolding,
    InsiderPledge,
    NextEvent,
    ShortLendingDay,
    ShortLendingMetrics,
)
from quant_signal.quant import short_data as calc

TAIPEI = ZoneInfo("Asia/Taipei")
LOOKBACK_DAYS = 50  # calendar days of history that cover 20 sessions plus the average window
REVISION_IGNORED = {"available_at", "revision", "source"}


def plan_revisions[T: BaseModel](rows: list[T], existing: dict[tuple, Any], key: Any) -> list[T]:
    """Rows to insert: new keys at revision 1, changed values at the next revision.

    ``existing`` maps a key to the newest stored row; identical re-publications are
    dropped so fetching the same data again never writes anything.
    """
    out: list[T] = []
    seen: dict[tuple, T] = {}
    for row in rows:
        k = key(row)
        previous = seen.get(k) or existing.get(k)
        if previous is None:
            new = row.model_copy(update={"revision": 1})
        else:
            fields = type(row).model_fields.keys() - REVISION_IGNORED
            if all(getattr(row, f) == getattr(previous, f) for f in fields):
                continue
            new = row.model_copy(update={"revision": previous.revision + 1})
        seen[k] = new
        out.append(new)
    return out


class ShortDataReadsMixin:
    # --- primitives implemented by each backend ----------------------------------------
    async def sd_sbl_rows(
        self, since: date, cutoff: datetime, symbol: str | None = None
    ) -> list[ShortLendingDay]:
        raise NotImplementedError

    async def sd_day_trade_rows(
        self, since: date, cutoff: datetime, symbol: str | None = None
    ) -> list[DayTradeDay]:
        raise NotImplementedError

    async def sd_latest_date(self, kind: str, cutoff: datetime) -> date | None:
        """Newest trading date visible at ``cutoff`` for ``"sbl"`` or ``"daytrade"``."""
        raise NotImplementedError

    async def sd_volume_rows(
        self, since: date, cutoff: datetime, symbol: str | None = None
    ) -> list[tuple[str, date, int]]:
        """``(symbol, trading_date, volume in shares)`` from ``market_stock_daily``."""
        raise NotImplementedError

    async def sd_insider_rows(
        self, cutoff: datetime, symbol: str | None = None
    ) -> list[InsiderHolding]:
        """Newest period per symbol, newest revision visible at ``cutoff``."""
        raise NotImplementedError

    async def sd_dividend_rows(
        self, start: date, end: date, cutoff: datetime, *, include_withdrawn: bool = False
    ) -> list[DividendEvent]:
        """Newest revision per event visible at ``cutoff``; withdrawn events are skipped
        unless ``include_withdrawn`` (a withdrawal is visible from its own ``available_at``)."""
        raise NotImplementedError

    async def upsert_dividend_events(self, rows: list[DividendEvent]) -> int:
        raise NotImplementedError

    async def withdraw_unlisted_dividend_events(
        self, source: str, listed: Iterable[DividendEvent], *, today: date, available_at: datetime
    ) -> int:
        """Retire stored future events of ``source`` that its latest forecast table dropped.

        ``listed`` is the complete table just fetched. Events with an ex-date after
        ``today`` and up to the table's last ex-date that it no longer lists get a new
        revision marked withdrawn (the old revisions stay for earlier as-of reads). Past
        ex-dates and events beyond the table's span are never touched; an empty table
        withdraws nothing (it may be a failed fetch).
        """
        keys = {(e.symbol, e.ex_date, e.kind) for e in listed}
        if not keys:
            return 0
        start, last = today + timedelta(days=1), max(day for _, day, _ in keys)
        if last < start:
            return 0
        stored = await self.sd_dividend_rows(start, last, available_at)
        gone = [
            row.model_copy(update={"withdrawn": True, "available_at": available_at})
            for row in stored
            if row.source == source and (row.symbol, row.ex_date, row.kind) not in keys
        ]
        return await self.upsert_dividend_events(gone) if gone else 0

    # --- shared helpers ----------------------------------------------------------------
    async def _sd_volumes(
        self, since: date, cutoff: datetime
    ) -> tuple[dict[str, dict[date, int]], list[date]]:
        volumes: dict[str, dict[date, int]] = {}
        sessions: set[date] = set()
        for symbol, day, volume in await self.sd_volume_rows(since, cutoff):
            volumes.setdefault(symbol, {})[day] = volume
            sessions.add(day)
        return volumes, sorted(sessions)

    # --- whole-market reads for the screener / AI context ------------------------------
    async def latest_short_lending_metrics(self, as_of: datetime) -> dict[str, ShortLendingMetrics]:
        """Per symbol, derived from its newest visible 借券賣出 row.

        ``sbl_pct_of_shares`` needs ``foreign_holding_daily.shares_outstanding`` and
        ``days_to_cover`` the volume history of ``market_stock_daily``; either is None
        when that input is missing.
        """
        last_day = await self.sd_latest_date("sbl", as_of)
        if last_day is None:
            return {}
        since = last_day - timedelta(days=LOOKBACK_DAYS)
        rows = await self.sd_sbl_rows(since, as_of)
        volumes, sessions = await self._sd_volumes(since, as_of)
        outstanding = {
            symbol: item.shares_outstanding
            for symbol, item in (await self.latest_foreign_holdings(as_of)).items()  # type: ignore[attr-defined]
        }
        by_symbol: dict[str, dict[date, ShortLendingDay]] = {}
        for row in rows:
            by_symbol.setdefault(row.symbol, {})[row.trading_date] = row
        out: dict[str, ShortLendingMetrics] = {}
        for symbol, days in sorted(by_symbol.items()):
            day = max(days)
            out[symbol] = short_lending_metrics(
                symbol, day, days, volumes.get(symbol, {}), sessions, outstanding.get(symbol)
            )
        return out

    async def latest_day_trade_metrics(self, as_of: datetime) -> dict[str, DayTradeMetrics]:
        last_day = await self.sd_latest_date("daytrade", as_of)
        if last_day is None:
            return {}
        since = last_day - timedelta(days=LOOKBACK_DAYS)
        rows = await self.sd_day_trade_rows(since, as_of)
        volumes, sessions = await self._sd_volumes(since, as_of)
        by_symbol: dict[str, dict[date, DayTradeDay]] = {}
        for row in rows:
            by_symbol.setdefault(row.symbol, {})[row.trading_date] = row
        return {
            symbol: day_trade_metrics(symbol, max(days), days, volumes.get(symbol, {}), sessions)
            for symbol, days in sorted(by_symbol.items())
        }

    async def latest_insider_pledges(self, as_of: datetime) -> dict[str, InsiderPledge]:
        """Newest visible month per symbol (insiders' pledged / total holding)."""
        return {
            row.symbol: InsiderPledge(
                symbol=row.symbol,
                period=row.period,
                pledge_ratio_pct=row.pledge_ratio_pct,
                holding_shares=row.holding_shares,
                pledged_shares=row.pledged_shares,
            )
            for row in sorted(await self.sd_insider_rows(as_of), key=lambda r: r.symbol)
        }

    async def next_dividend_events(self, as_of: datetime, days: int = 60) -> dict[str, NextEvent]:
        """Earliest ex-dividend / ex-rights date per symbol from ``as_of``'s date on."""
        start = as_of.astimezone(TAIPEI).date()  # the Taipei date, not the UTC one
        rows = await self.sd_dividend_rows(start, start + timedelta(days=days), as_of)
        best: dict[str, DividendEvent] = {}
        for row in sorted(rows, key=lambda r: (r.ex_date, r.kind)):
            best.setdefault(row.symbol, row)
        return {
            symbol: NextEvent(
                symbol=symbol,
                date=row.ex_date,
                kind=row.kind,
                cash_dividend=row.cash_dividend_per_share,
                stock_dividend=row.stock_dividend_per_share,
            )
            for symbol, row in sorted(best.items())
        }

    async def list_dividend_events(
        self, symbols: list[str], start: date, end: date, as_of: datetime
    ) -> list[DividendEvent]:
        """Structured events with ``start <= ex_date <= end`` known by ``as_of``.

        For crediting dividends: ``cash_dividend_per_share`` is NT$ per share and
        ``stock_dividend_per_share`` new shares per share held (None until announced).
        """
        wanted = {item.upper() for item in symbols}
        rows = await self.sd_dividend_rows(start, end, as_of)
        return sorted(
            (row for row in rows if row.symbol in wanted),
            key=lambda r: (r.ex_date, r.symbol, r.kind),
        )


def short_lending_metrics(
    symbol: str,
    day: date,
    rows: dict[date, ShortLendingDay],
    volumes: dict[date, int],
    sessions: list[date],
    shares_outstanding: int | None,
) -> ShortLendingMetrics:
    balances = {d: r.sbl_balance for d, r in rows.items()}
    balance = balances.get(day)
    return ShortLendingMetrics(
        symbol=symbol,
        trading_date=day,
        sbl_balance_shares=balance,
        sbl_change_1d_pct=calc.balance_change_pct(balances, sessions, day, 1),
        sbl_change_5d_pct=calc.balance_change_pct(balances, sessions, day, 5),
        sbl_change_20d_pct=calc.balance_change_pct(balances, sessions, day, 20),
        sbl_pct_of_shares=calc.pct(balance, shares_outstanding),
        sbl_sold_pct_of_volume=calc.pct(rows[day].sbl_sold, volumes.get(day)),
        days_to_cover=calc.days_to_cover(balance, calc.average_volume(volumes, sessions, day)),
    )


def day_trade_metrics(
    symbol: str,
    day: date,
    rows: dict[date, DayTradeDay],
    volumes: dict[date, int],
    sessions: list[date],
) -> DayTradeMetrics:
    shares = {d: r.day_trade_volume for d, r in rows.items()}
    row = rows[day]
    return DayTradeMetrics(
        symbol=symbol,
        trading_date=day,
        day_trade_ratio_pct=calc.pct(row.day_trade_volume, volumes.get(day)),
        day_trade_ratio_5d_pct=calc.window_ratio_pct(shares, volumes, sessions, day, 5),
        day_trade_ratio_20d_pct=calc.window_ratio_pct(shares, volumes, sessions, day, 20),
        day_trade_net_pnl_proxy=calc.net_pnl_proxy(
            row.day_trade_buy_amount, row.day_trade_sell_amount
        ),
    )
