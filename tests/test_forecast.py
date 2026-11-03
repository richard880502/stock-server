from datetime import UTC, date, datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient

from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.domain.models import Bar
from quant_signal.forecast import (
    ForecastRequest,
    bootstrap_paths,
    eligible_bars,
    forecast,
    future_sessions,
)
from quant_signal.infrastructure.memory import MemoryQuantRepository


@pytest.mark.asyncio
async def test_forecast_reproducible_and_future_excluded(trending_bars):
    as_of = trending_bars[200].trading_date
    request = ForecastRequest(symbol="2330.TW", as_of=as_of, lookback=60)
    report = await forecast(trending_bars, request)
    truncated = await forecast(trending_bars[:201], request)
    assert report.points == truncated.points
    assert report.evaluation == truncated.evaluation
    assert report.data_through == as_of
    assert all(p.trading_date > as_of and p.lower <= p.median <= p.upper for p in report.points)
    assert report.evaluation.windows > 0
    assert report.model == "bootstrap"


def test_late_revision_does_not_erase_available_observation(trending_bars):
    bar = trending_bars[0]
    revision = bar.model_copy(
        update={"revision": 2, "available_at": datetime(2030, 1, 1, tzinfo=UTC)}
    )
    assert eligible_bars([bar, revision], bar.trading_date) == [bar]


def test_taiwan_cutoff_and_holidays(trending_bars):
    bar = trending_bars[0].model_copy(update={"available_at": datetime(2025, 1, 1, 17, tzinfo=UTC)})
    assert eligible_bars([bar], date(2025, 1, 1)) == []
    assert future_sessions(date(2026, 11, 3), 5) == [date(2026, 11, d) for d in [4, 5, 6, 9, 10]]
    assert date(2026, 1, 1) not in future_sessions(date(2025, 12, 31), 5)


def test_constant_series_has_no_fabricated_spread():
    paths = bootstrap_paths(np.ones(60) * 100, 5)
    assert (paths == 100).all()


@pytest.mark.asyncio
async def test_missing_model_is_explicit(trending_bars):
    request = ForecastRequest(
        symbol="2330.TW", as_of=trending_bars[-1].trading_date, model="kronos"
    )
    with pytest.raises(ValueError, match="Kronos 尚未啟用"):
        await forecast(trending_bars, request)


def test_forecast_api_validation(trending_bars: list[Bar]):
    repo = MemoryQuantRepository()
    repo.bars = {"2330.TW": [b.model_copy(update={"symbol": "2330.TW"}) for b in trending_bars]}

    async def repository():
        yield repo

    app.dependency_overrides[get_repository] = repository
    try:
        with TestClient(app) as client:
            request = {
                "symbol": "2330.tw",
                "as_of": str(trending_bars[-1].trading_date),
                "lookback": 60,
            }
            result = client.post("/api/v1/forecasts", json=request)
            assert result.status_code == 200, result.text
            assert result.json()["symbol"] == "2330.TW"
            assert (
                client.post("/api/v1/forecasts", json={**request, "horizon": 500}).status_code
                == 422
            )
    finally:
        app.dependency_overrides.clear()
