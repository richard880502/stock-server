"""Pure helpers for valuation percentiles (the SQL read side mirrors these definitions)."""

from __future__ import annotations

from collections.abc import Sequence

from quant_signal.domain.fundamentals import (
    MIN_PERCENTILE_OBSERVATIONS,
    PERCENTILE_WINDOW,
    ValuationDay,
    ValuationMetrics,
)


def percentile_rank(window: Sequence[float | None], latest: float | None) -> float | None:
    """Share (0-100) of the non-missing ``window`` values that are <= ``latest``.

    ``window`` is the stock's newest observations *including* today's. None when today's
    value is missing or fewer than ``MIN_PERCENTILE_OBSERVATIONS`` values exist.
    """
    if latest is None:
        return None
    values = [value for value in window if value is not None]
    if len(values) < MIN_PERCENTILE_OBSERVATIONS:
        return None
    return round(sum(1 for value in values if value <= latest) / len(values) * 100, 2)


def valuation_metrics(
    rows: Sequence[ValuationDay], *, window: int = PERCENTILE_WINDOW
) -> ValuationMetrics | None:
    """Latest row of one symbol (any order) with percentiles over its newest ``window`` rows."""
    if not rows:
        return None
    ordered = sorted(rows, key=lambda row: row.trading_date, reverse=True)
    latest, recent = ordered[0], ordered[:window]
    pe = [row.pe_ratio for row in recent]
    pb = [row.pb_ratio for row in recent]
    dividend = [row.dividend_yield_pct for row in recent]
    return ValuationMetrics(
        symbol=latest.symbol,
        trading_date=latest.trading_date,
        pe_ratio=latest.pe_ratio,
        pb_ratio=latest.pb_ratio,
        dividend_yield_pct=latest.dividend_yield_pct,
        dividend_year=latest.dividend_year,
        fiscal_period=latest.fiscal_period,
        pe_percentile_250d=percentile_rank(pe, latest.pe_ratio),
        pb_percentile_250d=percentile_rank(pb, latest.pb_ratio),
        yield_percentile_250d=percentile_rank(dividend, latest.dividend_yield_pct),
        pe_observations=sum(value is not None for value in pe),
        pb_observations=sum(value is not None for value in pb),
        yield_observations=sum(value is not None for value in dividend),
    )


def weekly_sample(rows: Sequence[ValuationDay], *, limit: int = 60) -> list[ValuationDay]:
    """Last row of each ISO week, oldest first, at most the newest ``limit`` weeks."""
    by_week: dict[tuple[int, int], ValuationDay] = {}
    for row in sorted(rows, key=lambda item: item.trading_date):
        iso = row.trading_date.isocalendar()
        by_week[(iso.year, iso.week)] = row
    return list(by_week.values())[-limit:]
