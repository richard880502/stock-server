"""Pure derived metrics for securities-lending short selling and day trading.

Inputs are plain dicts keyed by session date so the same code serves one stock (REST) and
the whole market (screener). Share counts are shares; ratios are percent (0-100).
"""

from __future__ import annotations

from datetime import date

MIN_VOLUME_SESSIONS = 10  # days-to-cover needs at least this many sessions of volume
WINDOW_COVERAGE = 0.8  # a 5/20-session average needs this share of the window present


def pct(part: float | None, whole: float | None) -> float | None:
    if part is None or whole is None or whole <= 0:
        return None
    return round(part / whole * 100.0, 2)


def change_pct(now: float | None, before: float | None) -> float | None:
    """Percent change of a balance; None when the earlier balance is missing or zero."""
    if now is None or before is None or before <= 0:
        return None
    return round((now - before) / before * 100.0, 2)


def balance_change_pct(
    balances: dict[date, int | None], sessions: list[date], as_of: date, back: int
) -> float | None:
    """Change of the SBL balance versus ``back`` sessions earlier.

    ``sessions`` is the ascending trading calendar; ``as_of`` must be on it.
    """
    if as_of not in sessions:
        return None
    index = sessions.index(as_of)
    if index - back < 0:
        return None
    return change_pct(balances.get(as_of), balances.get(sessions[index - back]))


def average_volume(volumes: dict[date, int], sessions: list[date], as_of: date, n: int = 20):
    """Mean volume of the last ``n`` sessions ending at ``as_of`` (None below the minimum)."""
    if as_of not in sessions:
        return None
    index = sessions.index(as_of)
    window = [volumes[d] for d in sessions[max(0, index - n + 1) : index + 1] if d in volumes]
    if len(window) < MIN_VOLUME_SESSIONS:
        return None
    return sum(window) / len(window)


def days_to_cover(balance: int | None, avg_volume: float | None) -> float | None:
    """SBL balance (shares) divided by the 20-session average daily volume (shares)."""
    if balance is None or not avg_volume or avg_volume <= 0:
        return None
    return round(balance / avg_volume, 4)


def window_ratio_pct(
    numerators: dict[date, int | None],
    volumes: dict[date, int],
    sessions: list[date],
    as_of: date,
    n: int,
) -> float | None:
    """Volume-weighted day-trade ratio over the last ``n`` sessions ending at ``as_of``.

    Sum of day-trade shares over sum of volume on the sessions that have both; None when
    fewer than 80% of the window's sessions do.
    """
    if as_of not in sessions:
        return None
    index = sessions.index(as_of)
    window = sessions[max(0, index - n + 1) : index + 1]
    if len(window) < n:
        return None
    used = [d for d in window if numerators.get(d) is not None and volumes.get(d, 0) > 0]
    if len(used) < n * WINDOW_COVERAGE:
        return None
    return pct(sum(numerators[d] for d in used), sum(volumes[d] for d in used))


def net_pnl_proxy(buy_amount: int | None, sell_amount: int | None) -> int | None:
    """Aggregate day-trade sell amount minus buy amount (NT$).

    Before fees and the 0.15% day-trade tax, and the sum over every day trader on both
    sides of the market, so it is only a rough proxy of the crowd's gross result.
    """
    if buy_amount is None or sell_amount is None:
        return None
    return sell_amount - buy_amount
