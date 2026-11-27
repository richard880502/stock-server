"""Monthly revenue trend: rolling YoY, highs and growth streak. Pure arithmetic."""

from __future__ import annotations

from quant_signal.domain.fundamentals import MonthRevenuePoint, RevenueTrend
from quant_signal.domain.models import MonthlyRevenue
from quant_signal.quant.revenue import _ordinal, latest_by_period, revenue_metrics

MAX_MONTHS = 36


def _growth(current: float, base: float) -> float | None:
    return round((current / base - 1) * 100, 2) if base > 0 else None


def _rolling_yoy(by_month: dict[int, MonthlyRevenue], position: int, span: int) -> float | None:
    """Sum of the last ``span`` months vs the same months a year earlier (all must exist)."""
    now = [by_month.get(position - offset) for offset in range(span)]
    before = [by_month.get(position - offset - 12) for offset in range(span)]
    if any(row is None for row in now) or any(row is None for row in before):
        return None
    return _growth(
        sum(row.revenue for row in now if row), sum(row.revenue for row in before if row)
    )


def revenue_trend(
    rows: list[MonthlyRevenue], *, include_months: bool = True
) -> RevenueTrend | None:
    """Trend of one company from its (point-in-time visible) monthly rows, any order.

    Per-month ``yoy_pct`` / ``mom_pct`` are the published figures; a missing one is
    derived from the same/previous month when that row exists. ``yoy_3m_pct`` /
    ``yoy_12m_pct`` need all 3 / 12 months and their year-earlier twins. The streak counts
    consecutive months ending at the latest with YoY > 0; ``is_12m_high`` needs twelve
    consecutive months, ``is_history_high`` at least twelve months held in total.
    """
    by_period = latest_by_period(rows)
    if not by_period:
        return None
    by_month = {_ordinal(period): row for period, row in by_period.items()}

    def yoy(row: MonthlyRevenue) -> float | None:
        if row.yoy_pct is not None:
            return row.yoy_pct
        prior = by_month.get(_ordinal(row.period) - 12)
        return _growth(row.revenue, prior.revenue) if prior else None

    def mom(row: MonthlyRevenue) -> float | None:
        if row.mom_pct is not None:
            return row.mom_pct
        prior = by_month.get(_ordinal(row.period) - 1)
        return _growth(row.revenue, prior.revenue) if prior else None

    filled = [row.model_copy(update={"yoy_pct": yoy(row)}) for row in by_period.values()]
    base = revenue_metrics(filled)
    assert base is not None  # rows is non-empty
    latest_period = max(by_period)
    position = _ordinal(latest_period)
    history_high: bool | None = None
    if len(by_period) >= 12:
        history_high = by_period[latest_period].revenue >= max(
            r.revenue for r in by_period.values()
        )
    months = [
        MonthRevenuePoint(
            period=row.period, revenue=row.revenue, yoy_pct=yoy(row), mom_pct=mom(row)
        )
        for row in sorted(by_period.values(), key=lambda item: item.period)[-MAX_MONTHS:]
    ]
    return RevenueTrend(
        symbol=by_period[latest_period].symbol,
        latest_period=latest_period,
        months=months if include_months else [],
        yoy_3m_pct=_rolling_yoy(by_month, position, 3),
        yoy_12m_pct=_rolling_yoy(by_month, position, 12),
        is_12m_high=base.is_12m_high,
        is_history_high=history_high,
        history_months=len(by_period),
        growth_streak_months=base.consecutive_positive_yoy,
    )
