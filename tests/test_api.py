from fastapi.testclient import TestClient

from quant_signal.adapters.rest.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_llm_status_is_safe() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/llm/status")

    assert response.status_code == 200
    assert "api_key" not in response.json()
    assert set(response.json()) == {
        "enabled",
        "configured",
        "missing",
        "model",
        "base_url",
    }
