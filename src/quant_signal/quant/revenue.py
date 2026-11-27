"""Monthly revenue metrics. Pure arithmetic: no I/O, no LLM."""

from __future__ import annotations

from quant_signal.domain.models import MonthlyRevenue, RevenueMetrics

ACCELERATION_LOOKBACK = 3
HIGH_WINDOW = 12


def _ordinal(period: str) -> int:
    year, month = period.split("-")
    return int(year) * 12 + int(month) - 1


def latest_by_period(rows: list[MonthlyRevenue]) -> dict[str, MonthlyRevenue]:
    """One row per period; the newest revision / fetch wins when a period repeats."""
    by_period: dict[str, MonthlyRevenue] = {}
    for row in rows:
        current = by_period.get(row.period)
        if current is None or (row.revision, row.available_at) > (
            current.revision,
            current.available_at,
        ):
            by_period[row.period] = row
    return by_period


def revenue_metrics(rows: list[MonthlyRevenue]) -> RevenueMetrics | None:
    """Summarise one company's monthly revenue history (any order, any revisions).

    - ``yoy_pct`` / ``mom_pct`` / ``cumulative_yoy_pct``: the published figures of the
      latest month.
    - ``acceleration_pct``: this month's YoY minus the mean YoY of the three calendar
      months before it (None unless all three are present with a YoY).
    - ``consecutive_positive_yoy``: how many months in a row, ending at the latest,
      had YoY > 0 (a gap or missing YoY ends the streak).
    - ``is_12m_high``: latest revenue is the highest of the trailing twelve months;
      None until twelve consecutive months of history have accumulated.
    """
    if not rows:
        return None
    by_period = latest_by_period(rows)
    latest = by_period[max(by_period)]
    position = _ordinal(latest.period)
    by_month = {_ordinal(period): row for period, row in by_period.items()}

    previous = [by_month.get(position - offset) for offset in range(1, ACCELERATION_LOOKBACK + 1)]
    acceleration: float | None = None
    if latest.yoy_pct is not None and all(
        row is not None and row.yoy_pct is not None for row in previous
    ):
        mean = sum(row.yoy_pct for row in previous if row and row.yoy_pct is not None) / len(
            previous
        )
        acceleration = latest.yoy_pct - mean

    streak = 0
    cursor = position
    while (row := by_month.get(cursor)) is not None and row.yoy_pct is not None and row.yoy_pct > 0:
        streak += 1
        cursor -= 1

    window = [by_month.get(position - offset) for offset in range(HIGH_WINDOW)]
    is_high: bool | None = None
    if all(row is not None for row in window):
        is_high = latest.revenue >= max(row.revenue for row in window if row is not None)

    return RevenueMetrics(
        symbol=latest.symbol,
        period=latest.period,
        revenue=latest.revenue,
        yoy_pct=latest.yoy_pct,
        mom_pct=latest.mom_pct,
        cumulative_yoy_pct=latest.cumulative_yoy_pct,
        acceleration_pct=acceleration,
        consecutive_positive_yoy=streak,
        is_12m_high=is_high,
        months_available=len(by_period),
        available_at=latest.available_at,
    )
