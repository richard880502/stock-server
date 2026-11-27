"""Postgres storage and point-in-time reads for valuation, revenue history and quarterly
financials. Every read is lean: it selects plain columns (no ORM entities) and, for the
whole-market reads, returns one small object per symbol."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import String, bindparam, func, select, text, tuple_
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession

from quant_signal.domain.fundamentals import (
    MIN_PERCENTILE_OBSERVATIONS,
    PERCENTILE_WINDOW,
    EarningsMetrics,
    QuarterlyFinancial,
    RevenueTrend,
    ValuationDay,
    ValuationMetrics,
)
from quant_signal.domain.models import MonthlyRevenue
from quant_signal.infrastructure.db.models import (
    MonthlyRevenueRow,
    QuarterlyFinancialRow,
    ValuationDailyRow,
)
from quant_signal.infrastructure.revisions import plan_revisions
from quant_signal.quant.quarterly import earnings_metrics
from quant_signal.quant.revenue_trend import revenue_trend

TAIPEI = ZoneInfo("Asia/Taipei")
CHUNK = 500
VALUATION_LOOKBACK_DAYS = 430  # comfortably more than 250 sessions
REVENUE_LOOKBACK_MONTHS = 40  # 36 shown + the year-earlier twins of the oldest rolling window
FINANCIAL_LOOKBACK_YEARS = 3


def _month_floor(day: date, months: int) -> str:
    index = day.year * 12 + day.month - 1 - months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


class FundamentalsRepositoryMixin:
    session: AsyncSession

    async def _insert_ignoring_conflicts(self, row_cls: type, rows: list[BaseModel]) -> int:
        written = 0
        for offset in range(0, len(rows), CHUNK):
            values = [row.model_dump(mode="python") for row in rows[offset : offset + CHUNK]]
            statement = insert(row_cls).values(values).on_conflict_do_nothing()
            written += (await self.session.execute(statement)).rowcount or 0
        await self.session.commit()
        return written

    # --- valuation ---------------------------------------------------------------

    async def upsert_valuation_daily(self, rows: list[ValuationDay]) -> int:
        """Insert new (symbol, date, source) rows; published days are final, so no update."""
        return await self._insert_ignoring_conflicts(ValuationDailyRow, rows) if rows else 0

    async def latest_valuation_metrics(
        self,
        as_of: datetime,
        symbols: list[str] | None = None,
        *,
        window: int = PERCENTILE_WINDOW,
    ) -> dict[str, ValuationMetrics]:
        """Newest visible valuation per symbol with percentiles over its own newest
        ``window`` sessions.

        Only each symbol's newest ``window`` rows are ever read: one primary-key probe for
        the newest row and one backward range scan limited to ``window`` rows per symbol
        (a lateral join over the symbol list; the whole-market symbol list itself is found
        with an index skip-scan). Ranking every row of the lookback range and joining it
        back to itself took ~0.6 s at full history; this does no sort and no self-join.
        """
        if symbols is not None and not symbols:
            return {}
        if symbols is None:
            symbol_source = """
                WITH RECURSIVE syms(symbol) AS (
                    (SELECT min(symbol) FROM valuation_daily)
                    UNION ALL
                    SELECT (SELECT min(v.symbol) FROM valuation_daily v WHERE v.symbol > s.symbol)
                    FROM syms s WHERE s.symbol IS NOT NULL
                )"""
        else:
            symbol_source = """
                WITH syms(symbol) AS (SELECT unnest(CAST(:symbols AS text[])))"""
        visible = """
            v.symbol = s.symbol AND v.available_at <= :as_of
            AND v.trading_date <= :day AND v.trading_date >= :floor"""
        statement = text(
            f"""{symbol_source}
            SELECT s.symbol, n.trading_date, n.pe_ratio, n.pb_ratio, n.dividend_yield_pct,
                   n.dividend_year, n.fiscal_period,
                   a.pe_n, a.pe_le, a.pb_n, a.pb_le, a.yd_n, a.yd_le
            FROM syms s
            CROSS JOIN LATERAL (
                SELECT v.trading_date, v.pe_ratio, v.pb_ratio, v.dividend_yield_pct,
                       v.dividend_year, v.fiscal_period
                FROM valuation_daily v WHERE {visible}
                ORDER BY v.trading_date DESC LIMIT 1
            ) n
            CROSS JOIN LATERAL (
                SELECT count(r.pe_ratio) AS pe_n,
                       count(r.pe_ratio) FILTER (WHERE r.pe_ratio <= n.pe_ratio) AS pe_le,
                       count(r.pb_ratio) AS pb_n,
                       count(r.pb_ratio) FILTER (WHERE r.pb_ratio <= n.pb_ratio) AS pb_le,
                       count(r.dividend_yield_pct) AS yd_n,
                       count(r.dividend_yield_pct)
                           FILTER (WHERE r.dividend_yield_pct <= n.dividend_yield_pct) AS yd_le
                FROM (
                    SELECT v.pe_ratio, v.pb_ratio, v.dividend_yield_pct
                    FROM valuation_daily v WHERE {visible}
                    ORDER BY v.trading_date DESC LIMIT :window
                ) r
            ) a
            WHERE s.symbol IS NOT NULL
            ORDER BY s.symbol"""
        )
        day = as_of.astimezone(TAIPEI).date()
        params: dict[str, object] = {
            "as_of": as_of,
            "day": day,
            "floor": day - timedelta(days=VALUATION_LOOKBACK_DAYS),
            "window": window,
        }
        if symbols is not None:
            statement = statement.bindparams(bindparam("symbols", type_=ARRAY(String)))
            params["symbols"] = sorted({symbol.upper() for symbol in symbols})
        result: dict[str, ValuationMetrics] = {}
        for record in (await self.session.execute(statement, params)).all():
            symbol, trading_date, pe, pb, dividend, year, period = record[:7]
            (pe_n, pe_le), (pb_n, pb_le), (yd_n, yd_le) = (
                record[7:9],
                record[9:11],
                record[11:13],
            )

            def pct(latest: float | None, total: int, at_most: int) -> float | None:
                if latest is None or total < MIN_PERCENTILE_OBSERVATIONS:
                    return None
                return round(at_most / total * 100, 2)

            result[symbol] = ValuationMetrics(
                symbol=symbol,
                trading_date=trading_date,
                pe_ratio=pe,
                pb_ratio=pb,
                dividend_yield_pct=dividend,
                dividend_year=year,
                fiscal_period=period,
                pe_percentile_250d=pct(pe, pe_n, pe_le),
                pb_percentile_250d=pct(pb, pb_n, pb_le),
                yield_percentile_250d=pct(dividend, yd_n, yd_le),
                pe_observations=pe_n,
                pb_observations=pb_n,
                yield_observations=yd_n,
            )
        return result

    async def valuation_history(
        self, symbol: str, as_of: datetime, sessions: int = 300
    ) -> list[ValuationDay]:
        """The symbol's visible daily rows, oldest first, at most the newest ``sessions``."""
        cls = ValuationDailyRow
        statement = (
            select(cls)
            .where(cls.symbol == symbol.upper(), cls.available_at <= as_of)
            .order_by(cls.trading_date.desc())
            .limit(sessions)
        )
        rows = (await self.session.execute(statement)).scalars().all()
        items = [
            ValuationDay.model_validate(
                {name: getattr(row, name) for name in ValuationDay.model_fields}
            )
            for row in rows
        ]
        return sorted(items, key=lambda item: item.trading_date)

    # --- monthly revenue history -------------------------------------------------

    async def insert_missing_monthly_revenue(self, rows: list[MonthlyRevenue]) -> int:
        """Backfill: add (symbol, period) months we hold in no revision; never revise."""
        if not rows:
            return 0
        cls = MonthlyRevenueRow
        keys = list({(row.symbol, row.period) for row in rows})
        held: set[tuple[str, str]] = set()
        for offset in range(0, len(keys), CHUNK):
            statement = select(cls.symbol, cls.period).where(
                tuple_(cls.symbol, cls.period).in_(keys[offset : offset + CHUNK])
            )
            held.update(
                (symbol, period) for symbol, period in await self.session.execute(statement)
            )
        fresh = [row for row in rows if (row.symbol, row.period) not in held]
        return await self._insert_ignoring_conflicts(cls, fresh) if fresh else 0

    async def revenue_trends(
        self, as_of: datetime, symbols: list[str] | None = None, *, include_months: bool = False
    ) -> dict[str, RevenueTrend]:
        """Revenue trend per symbol from the newest 40 visible months (one query)."""
        if symbols is not None and not symbols:
            return {}
        cls = MonthlyRevenueRow
        day = as_of.astimezone(TAIPEI).date()
        columns = (
            cls.symbol,
            cls.period,
            cls.revision,
            cls.revenue,
            cls.mom_pct,
            cls.yoy_pct,
            cls.cumulative_revenue,
            cls.cumulative_yoy_pct,
            cls.source,
            cls.available_at,
        )
        inner = select(
            *columns,
            func.row_number()
            .over(partition_by=[cls.symbol, cls.period], order_by=cls.revision.desc())
            .label("rn"),
        ).where(
            cls.available_at <= as_of,
            cls.period >= _month_floor(day, REVENUE_LOOKBACK_MONTHS),
        )
        if symbols is not None:
            inner = inner.where(cls.symbol.in_([symbol.upper() for symbol in symbols]))
        ranked = inner.subquery()
        statement = select(*[ranked.c[column.key] for column in columns]).where(ranked.c.rn == 1)
        by_symbol: dict[str, list[MonthlyRevenue]] = {}
        for record in (await self.session.execute(statement)).mappings():
            row = MonthlyRevenue.model_construct(**dict(record))
            by_symbol.setdefault(row.symbol, []).append(row)
        result: dict[str, RevenueTrend] = {}
        for symbol in sorted(by_symbol):
            trend = revenue_trend(by_symbol[symbol], include_months=include_months)
            if trend is not None:
                result[symbol] = trend
        return result

    # --- quarterly financials ----------------------------------------------------

    async def _visible_financials(
        self, as_of: datetime, symbols: list[str] | None
    ) -> dict[str, list[QuarterlyFinancial]]:
        cls = QuarterlyFinancialRow
        fields = list(QuarterlyFinancial.model_fields)
        inner = select(
            *[getattr(cls, name) for name in fields],
            func.row_number()
            .over(
                partition_by=[cls.symbol, cls.fiscal_year, cls.fiscal_quarter],
                order_by=cls.revision.desc(),
            )
            .label("rn"),
        ).where(
            cls.available_at <= as_of,
            cls.fiscal_year >= as_of.astimezone(TAIPEI).year - FINANCIAL_LOOKBACK_YEARS,
        )
        if symbols is not None:
            inner = inner.where(cls.symbol.in_([symbol.upper() for symbol in symbols]))
        ranked = inner.subquery()
        statement = select(*[ranked.c[name] for name in fields]).where(ranked.c.rn == 1)
        by_symbol: dict[str, list[QuarterlyFinancial]] = {}
        for record in (await self.session.execute(statement)).mappings():
            row = QuarterlyFinancial.model_construct(**dict(record))
            by_symbol.setdefault(row.symbol, []).append(row)
        return by_symbol

    async def _existing_financial_keys(
        self, keys: list[tuple[str, int, int]]
    ) -> dict[tuple[str, int, int], QuarterlyFinancial]:
        """Highest stored revision for each incoming (symbol, year, quarter)."""
        cls = QuarterlyFinancialRow
        found: dict[tuple[str, int, int], QuarterlyFinancial] = {}
        for offset in range(0, len(keys), CHUNK):
            statement = select(cls).where(
                tuple_(cls.symbol, cls.fiscal_year, cls.fiscal_quarter).in_(
                    keys[offset : offset + CHUNK]
                )
            )
            for stored in (await self.session.execute(statement)).scalars():
                item = QuarterlyFinancial.model_validate(
                    {name: getattr(stored, name) for name in QuarterlyFinancial.model_fields}
                )
                key = (item.symbol, item.fiscal_year, item.fiscal_quarter)
                if key not in found or item.revision > found[key].revision:
                    found[key] = item
        return found

    async def upsert_quarterly_financials(self, rows: list[QuarterlyFinancial]) -> int:
        """Forward ingest: new quarters at revision 1, restated figures at revision + 1.

        The ``source`` is not part of "did it change", so the same numbers arriving from
        the OpenAPI after a MOPS backfill (or vice versa) create no revision.
        """
        if not rows:
            return 0
        keys = list({(row.symbol, row.fiscal_year, row.fiscal_quarter) for row in rows})
        existing = await self._existing_financial_keys(keys)
        planned = plan_revisions(
            existing,
            rows,
            lambda row: (row.symbol, row.fiscal_year, row.fiscal_quarter),
            ignore={"source"},
        )
        return await self._insert_ignoring_conflicts(QuarterlyFinancialRow, planned)

    async def insert_missing_quarterly_financials(self, rows: list[QuarterlyFinancial]) -> int:
        """Backfill: add company-quarters held in no revision; never revise."""
        if not rows:
            return 0
        keys = list({(row.symbol, row.fiscal_year, row.fiscal_quarter) for row in rows})
        held = await self._existing_financial_keys(keys)
        fresh = [
            row for row in rows if (row.symbol, row.fiscal_year, row.fiscal_quarter) not in held
        ]
        return await self._insert_ignoring_conflicts(QuarterlyFinancialRow, fresh) if fresh else 0

    async def quarterly_financials(self, symbol: str, as_of: datetime) -> list[QuarterlyFinancial]:
        """The symbol's visible statements (latest revisions, last ~3 years), oldest first."""
        rows = (await self._visible_financials(as_of, [symbol])).get(symbol.upper(), [])
        return sorted(rows, key=lambda row: (row.fiscal_year, row.fiscal_quarter))

    async def earnings_summaries(
        self, as_of: datetime, symbols: list[str] | None = None
    ) -> dict[str, EarningsMetrics]:
        """Earnings summary of every symbol's newest visible quarter."""
        if symbols is not None and not symbols:
            return {}
        by_symbol = await self._visible_financials(as_of, symbols)
        result: dict[str, EarningsMetrics] = {}
        for symbol in sorted(by_symbol):
            metrics = earnings_metrics(by_symbol[symbol])
            if metrics is not None:
                result[symbol] = metrics
        return result
