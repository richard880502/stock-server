"""The request matrix the sector golden capture covers (run against any implementation)."""

from __future__ import annotations

from datetime import date
from typing import Any

from market_perf_fixture import MONDAY_AFTER_CLOSE, MONDAY_IN_SESSION, build_repository

from quant_signal.application.market_sectors import SectorRotationService


async def capture() -> dict[str, Any]:
    out: dict[str, Any] = {}
    scenarios = {
        "after_close": (MONDAY_AFTER_CLOSE, None, None),
        "intraday_live": (MONDAY_IN_SESSION, 0.9, None),
        "intraday_thin": (MONDAY_IN_SESSION, 0.2, None),
        "historic": (MONDAY_AFTER_CLOSE, None, date(2026, 10, 20)),
        "historic_future_day": (MONDAY_AFTER_CLOSE, None, date(2026, 11, 20)),
    }
    for name, (now, share, as_of) in scenarios.items():
        repo = await build_repository(snapshots=share)
        service = SectorRotationService(repo, clock=lambda now=now: now)
        for window in (1, 5, 20):
            result = await service.rotation(window=window, as_of=as_of)
            out[f"{name}/rotation/{window}"] = result.model_dump(mode="json")
        for code in ("24", "14", "26", "nope"):
            for sort in ("window_return", "day_change", "turnover", "institutional"):
                for order in ("desc", "asc"):
                    stocks = await service.stocks(
                        code, window=5, as_of=as_of, sort=sort, order=order, limit=7
                    )
                    out[f"{name}/stocks/{code}/{sort}/{order}"] = stocks.model_dump(mode="json")
    empty = SectorRotationService((await build_repository(0)), clock=lambda: MONDAY_AFTER_CLOSE)
    out["empty/rotation"] = (await empty.rotation(window=5)).model_dump(mode="json")
    out["empty/stocks"] = (await empty.stocks("24")).model_dump(mode="json")
    return out
