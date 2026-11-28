"""Definitions of the whole-market ranking metrics, shared by every repository.

A metric is the sum (or first-to-last move) of one column over the stocks' sessions
in the window. SQL aggregates the raw sum; :func:`finalize_ranking_value` turns it
into the unit shown to users, so the Postgres and in-memory repositories agree.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

SHARES_PER_LOT = 1000
# Metrics summed from a column counted in shares (shown in lots).
SHARE_COLUMNS: dict[str, str] = {
    "foreign_net": "foreign_net",
    "trust_net": "trust_net",
    "dealer_net": "dealer_net",
    "total_net": "total_net",
}
# Metrics summed from a column already counted in lots.
LOT_COLUMNS: dict[str, str] = {"margin_change": "margin_change", "short_change": "short_change"}
# Reports the exchanges publish after the quotes; a session only counts for these
# metrics once the report exists.
REQUIRED_REPORT: dict[str, str] = {
    "foreign_net": "foreign_net",
    "trust_net": "trust_net",
    "dealer_net": "dealer_net",
    "total_net": "total_net",
    "margin_change": "margin_change",
    "short_change": "short_change",
}


def finalize_ranking_value(metric: str, raw: Any) -> float:
    """Convert an aggregate (integer sum, float sum or percent) to the displayed unit."""
    if metric in SHARE_COLUMNS or metric == "volume":
        return int(raw) / SHARES_PER_LOT
    if metric in LOT_COLUMNS:
        return float(int(raw))
    return float(raw)


def ranking_raw_value(metric: str, history: Sequence[Any]) -> float | int | None:
    """The metric's aggregate over one stock's rows, oldest first (None: nothing known)."""
    if metric in SHARE_COLUMNS or metric in LOT_COLUMNS:
        column = SHARE_COLUMNS.get(metric) or LOT_COLUMNS[metric]
        known = [v for r in history if (v := getattr(r, column)) is not None]
        return sum(known) if known else None
    if metric == "volume":
        return sum(r.volume for r in history)
    if metric == "turnover":
        return sum(r.turnover_value for r in history)
    if metric == "change_pct":
        # The oldest day's reference price is its close minus its change, so the
        # result is the move over the whole window including that day.
        base = history[0].close - history[0].change
        return (history[-1].close / base - 1) * 100 if base > 0 else None
    raise ValueError(f"unknown ranking metric: {metric}")
