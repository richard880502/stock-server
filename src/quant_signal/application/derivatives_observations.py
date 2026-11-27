"""Feeds the official foreign TX-equivalent net open interest into market observations.

``market_observations_daily.futures_net_open_interest`` used to be a hard-coded 0. Rows
built before the futures data was fetched get a new *revision* (same ``source``/date,
``revision + 1``, same ``available_at`` = the documented publication time) so as-of
reads keep working and the previous value stays in the table.
"""

from __future__ import annotations

import math
from typing import Any

from quant_signal.domain.models import MarketObservation
from quant_signal.quant.point_in_time import source_priority

MARKET = "TW"


async def refresh_observation_futures(repository: Any, *, market: str = MARKET) -> int:
    """Revise stored production observations whose futures figure differs; returns rows written."""
    if market != MARKET:  # TX positions describe the TAIEX futures only
        return 0
    stored: list[MarketObservation] = await repository.list_market_observations(market)
    latest: dict[tuple, MarketObservation] = {}
    for item in stored:
        if source_priority(item.source) <= 0:
            continue
        key = (item.trading_date, item.source)
        if key not in latest or item.revision > latest[key].revision:
            latest[key] = item
    if not latest:
        return 0
    dates = [item.trading_date for item in latest.values()]
    series = await repository.foreign_tx_equiv_net_oi_series(min(dates), max(dates))
    revisions = [
        item.model_copy(
            update={
                "futures_net_open_interest": float(series[item.trading_date]),
                "futures_oi_known": True,
                "revision": item.revision + 1,
            }
        )
        for item in latest.values()
        if item.trading_date in series
        and (
            not item.futures_oi_known
            or not math.isclose(
                item.futures_net_open_interest, series[item.trading_date], abs_tol=1e-9
            )
        )
    ]
    return await repository.upsert_market_observations(revisions) if revisions else 0
