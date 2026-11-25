from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.application.stocks import PriceSeriesNotFound, PriceSeriesService
from quant_signal.domain.models import Bar
from quant_signal.infrastructure.memory import MemoryQuantRepository

DAYS = [date(2026, 11, 2) + timedelta(days=i) for i in range(5)]


def _bar(day: date, *, close: float, factor: float = 1.0, revision: int = 1, **kw) -> Bar:
    fields = {
        "symbol": "2330.TW",
        "trading_date": day,
        "open": close - 1,
        "high": close + 2,
        "low": close - 2,
        "close": close,
        "adjustment_factor": factor,
        "adjusted_close": close * factor,
        "volume": 1_000 * (revision + 1),
        "available_at": datetime.combine(day, time(8), tzinfo=UTC),
        "source": "twse_official",
        "revision": revision,
    }
    fields.update(kw)
    return Bar(**fields)


@pytest.fixture
async def repo() -> MemoryQuantRepository:
    repository = MemoryQuantRepository()
    # An ex-dividend event on DAYS[3] lifts the forward factor from 1.0 to 1.1.
    await repository.upsert_bars(
        [
            _bar(DAYS[0], close=100),
            _bar(DAYS[1], close=102),
            _bar(DAYS[1], close=103, revision=2),  # a later correction wins
            _bar(DAYS[2], close=104),
            _bar(DAYS[3], close=99, factor=1.1),
            _bar(DAYS[4], close=100, factor=1.1),
        ]
    )
    return repository


async def test_back_adjusted_series_keeps_latest_price_real(repo):
    series = await PriceSeriesService(repo).get("2330.tw")

    assert series.symbol == "2330.TW"
    assert [bar.trading_date for bar in series.bars] == DAYS
    assert series.bars[-1].close == 100  # latest bar is the real traded price
    assert series.bars[0].close == pytest.approx(100 / 1.1, abs=1e-3)
    assert series.bars[1].close == pytest.approx(103 / 1.1, abs=1e-3)  # revision 2
    assert series.bars[1].volume == 3_000


async def test_raw_series_is_unscaled(repo):
    series = await PriceSeriesService(repo).get("2330.TW", adjust="none")

    assert [bar.close for bar in series.bars] == [100, 103, 104, 99, 100]


async def test_as_of_hides_later_bars_and_their_adjustments(repo):
    series = await PriceSeriesService(repo).get("2330.TW", as_of=DAYS[2])

    assert [bar.trading_date for bar in series.bars] == DAYS[:3]
    # The ex-dividend event is in the future as of DAYS[2]: no rescaling yet.
    assert [bar.close for bar in series.bars] == [100, 103, 104]


async def test_unknown_symbol_raises(repo):
    with pytest.raises(PriceSeriesNotFound):
        await PriceSeriesService(repo).get("9999.TW")


def test_http_route(repo):
    async def override():
        yield repo

    app.dependency_overrides[get_repository] = override
    try:
        with TestClient(app) as client:
            ok = client.get("/api/v1/bars/2330.TW", params={"start": DAYS[3].isoformat()})
            assert ok.status_code == 200
            body = ok.json()
            assert body["adjust"] == "back"
            assert [bar["trading_date"] for bar in body["bars"]] == [
                DAYS[3].isoformat(),
                DAYS[4].isoformat(),
            ]
            assert client.get("/api/v1/bars/9999.TW").status_code == 404
            assert client.get("/api/v1/bars/bad%20sym").status_code == 422
    finally:
        app.dependency_overrides.clear()
