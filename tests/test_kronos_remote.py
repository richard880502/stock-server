from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from conftest import make_bars
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import quant_signal.forecast as forecast_module
from quant_signal.forecast import ForecastRequest, forecast

KRONOS_DIR = Path(__file__).resolve().parents[1] / "kronos-service"


def _mock_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(forecast_module.httpx, "AsyncClient", factory)


def _request(bars) -> ForecastRequest:
    return ForecastRequest(
        symbol="2330.TW", as_of=bars[-1].trading_date, horizon=1, lookback=120, model="kronos"
    )


async def test_remote_kronos_receives_the_bearer_key(monkeypatch) -> None:
    bars = make_bars(symbol="2330.TW", count=200)
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"paths": [[100.0 + i] for i in range(16)]})

    _mock_client(monkeypatch, handler)
    report = await forecast(
        bars, _request(bars), kronos_url="https://kronos.example/", kronos_api_key="s3cret"
    )

    assert seen == {"auth": "Bearer s3cret", "url": "https://kronos.example/predict"}
    assert report.model == "kronos" and len(report.points) == 1


async def test_no_authorization_header_when_no_key_is_configured(monkeypatch) -> None:
    bars = make_bars(symbol="2330.TW", count=200)
    headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"paths": [[100.0]] * 16})

    _mock_client(monkeypatch, handler)
    await forecast(bars, _request(bars), kronos_url="http://kronos:8002")

    assert headers == [None]


async def test_a_rejected_key_is_reported_as_a_configuration_error(monkeypatch) -> None:
    bars = make_bars(symbol="2330.TW", count=200)
    _mock_client(monkeypatch, lambda request: httpx.Response(401, json={"detail": "no"}))

    with pytest.raises(ValueError, match="KRONOS_API_KEY"):
        await forecast(bars, _request(bars), kronos_url="http://kronos:8002", kronos_api_key="x")


def _auth_app(monkeypatch: pytest.MonkeyPatch, key: str | None) -> TestClient:
    monkeypatch.syspath_prepend(str(KRONOS_DIR))
    sys.modules.pop("auth", None)
    import auth  # the service's own tiny module; it needs only fastapi

    if key is None:
        monkeypatch.delenv("KRONOS_API_KEY", raising=False)
    else:
        monkeypatch.setenv("KRONOS_API_KEY", key)
    app = FastAPI()

    @app.post("/predict", dependencies=[Depends(auth.require_api_key)])
    def predict() -> dict:
        return {"ok": True}

    return TestClient(app)


def test_service_stays_open_when_no_key_is_set(monkeypatch) -> None:
    assert _auth_app(monkeypatch, None).post("/predict").status_code == 200


def test_service_requires_the_key_when_set(monkeypatch) -> None:
    client = _auth_app(monkeypatch, "s3cret")

    assert client.post("/predict").status_code == 401
    assert client.post("/predict", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/predict", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.post("/predict", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.post("/predict", headers={"Authorization": "s3cret"}).status_code == 401
