"""AI-proposal REST routes: contract shape, status codes, quota, accepting through orders."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from itertools import count
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from paper_proposal_support import FakeLLM, FixedContext, answer, real_context
from paper_support import FRI, at, market_repo, quote

from quant_signal.adapters.rest import paper_proposals as routes
from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.adapters.rest.security import Principal, _get_repository, require_principal
from quant_signal.application import paper_trading
from quant_signal.application.data_sync import DataSyncPending
from quant_signal.application.llm_analysis import AnalysisContextService
from quant_signal.domain.models import User
from quant_signal.settings import get_settings

ME, YOU = uuid4(), uuid4()
BASE = "/api/v1/paper"


def _user(user_id: UUID) -> User:
    return User(
        id=user_id,
        google_sub=f"g-{user_id}",
        email=f"{user_id}@x.com",
        created_at=datetime.now(UTC),
    )


class Who:
    principal: Principal = Principal("user", user=_user(ME))


class Setup:
    llm = FakeLLM(answer())
    repo = None
    context = None


@pytest.fixture
async def repo():
    repository = await market_repo()
    repository.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    return repository


@pytest.fixture
async def client(repo, monkeypatch):
    Who.principal = Principal("user", user=_user(ME))
    context = await real_context(repo)
    Setup.llm = FakeLLM(answer())

    async def override():
        yield repo

    async def build(self, **kwargs):
        if Setup.build_error is not None:
            raise Setup.build_error
        return context

    Setup.build_error = None
    real = paper_trading.PaperService.__init__
    ticks = count()

    def pinned(self, *args, **kwargs):
        kwargs.setdefault("clock", lambda: at(FRI, 10, 0) + timedelta(milliseconds=next(ticks)))
        real(self, *args, **kwargs)

    settings = get_settings()
    monkeypatch.setattr(paper_trading.PaperService, "__init__", pinned)
    monkeypatch.setattr(AnalysisContextService, "build", build)
    monkeypatch.setattr(type(settings), "llm_errors", lambda self: [])
    monkeypatch.setattr(routes, "create_llm_client", lambda _settings: Setup.llm)
    app.dependency_overrides[get_repository] = override
    app.dependency_overrides[_get_repository] = override  # the quota counter
    app.dependency_overrides[require_principal] = lambda: Who.principal
    with TestClient(app) as c:
        c.context = context
        c.repo = repo
        yield c
    app.dependency_overrides.clear()


Setup.build_error = None


def _account(client, name="主帳戶") -> str:
    response = client.post(f"{BASE}/accounts", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _create(client, account, symbol="1101.TW"):
    return client.post(f"{BASE}/accounts/{account}/proposals", json={"symbol": symbol})


def test_create_returns_the_contract_shape(client) -> None:
    account = _account(client)
    response = _create(client, account)
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {
        "id", "account_id", "symbol", "name", "as_of", "created_at", "expires_at", "status",
        "action", "order", "stop_loss", "take_profit", "horizon_days", "headline", "rationale",
        "key_evidence", "risks", "invalidation", "evidence_refs", "confidence", "signal",
        "aligned", "checks", "sizing", "reference_price", "model", "prompt_version",
        "order_id", "decided_at", "reject_reason",
    }  # fmt: skip
    assert body["account_id"] == account and body["symbol"] == "1101.TW"
    assert body["status"] == "pending" and body["action"] == "buy"
    assert body["order"] == {
        "side": "buy",
        "order_type": "limit",
        "limit_price": 100.0,
        "lots": 1,
        "est_amount": 100000,
    }
    assert set(body["signal"]) == {"action", "score", "as_of"}
    assert set(body["sizing"]) == {
        "equity", "cash_available", "position_lots", "risk_amount", "risk_pct", "position_pct",
    }  # fmt: skip
    assert body["expires_at"] == "2026-11-27T05:30:00Z"  # 13:30 Taipei
    assert body["as_of"] == "2026-11-26" and body["order_id"] is None
    assert body["model"] == "fake-trader" and body["prompt_version"] == "trade_proposal_v1"
    # the exact served object is kept for the report / frontend fixtures
    print(json.dumps(body, ensure_ascii=False, indent=2))


def test_create_consumes_one_llm_use(client) -> None:
    account = _account(client)
    assert sum(client.repo.usage.values()) == 0
    assert _create(client, account).status_code == 201
    assert list(client.repo.usage.values()) == [1]
    # invalid model output is retried inside the one use
    Setup.llm = FakeLLM({"action": "buy"}, answer())
    assert _create(client, account).status_code == 201
    assert list(client.repo.usage.values()) == [2]
    assert len(Setup.llm.calls) == 2


def test_quota_exceeded_is_429(client, monkeypatch) -> None:
    account = _account(client)
    monkeypatch.setattr(get_settings(), "quota_llm_per_day", 1)
    assert _create(client, account).status_code == 201
    response = _create(client, account)
    assert response.status_code == 429 and "Retry-After" in response.headers


def test_refused_requests_do_not_use_quota(client, monkeypatch) -> None:
    account = _account(client)
    monkeypatch.setattr(get_settings(), "paper_proposals_enabled", False)
    response = _create(client, account)
    assert response.status_code == 503 and "AI 提案" in response.json()["detail"]
    monkeypatch.setattr(get_settings(), "paper_proposals_enabled", True)
    monkeypatch.setattr(type(get_settings()), "llm_errors", lambda self: ["ANTHROPIC_API_KEY"])
    response = _create(client, account)
    assert response.status_code == 503 and "not configured" in response.json()["detail"]
    assert client.repo.usage == {}
    # reading keeps working while creation is off
    assert client.get(f"{BASE}/accounts/{account}/proposals").status_code == 200


def test_disabled_proposals_still_list_and_get(client, monkeypatch) -> None:
    account = _account(client)
    proposal = _create(client, account).json()
    monkeypatch.setattr(get_settings(), "paper_proposals_enabled", False)
    assert client.get(f"{BASE}/accounts/{account}/proposals/{proposal['id']}").status_code == 200


def test_error_mapping(client) -> None:
    account = _account(client)
    assert _create(client, account, "9999").status_code == 404
    assert _create(client, account, "not a symbol").status_code == 422
    assert client.post(f"{BASE}/accounts/{account}/proposals", json={}).status_code == 422
    assert _create(client, str(uuid4())).status_code == 404
    Setup.build_error = LookupError("no data")
    assert _create(client, account).status_code == 404
    Setup.build_error = DataSyncPending("1101.TW")
    pending = _create(client, account)
    assert pending.status_code == 503
    assert pending.json()["sync_pending"] is True and pending.json()["symbol"] == "1101.TW"
    assert "Retry-After" in pending.headers
    Setup.build_error = None
    Setup.llm = FakeLLM({"action": "buy"})
    bad = _create(client, account)
    assert bad.status_code == 502 and "格式" in bad.json()["detail"]


def test_routes_are_for_signed_in_users_only(client) -> None:
    account = _account(client)
    for principal in (Principal("anonymous"), Principal("api_key")):
        Who.principal = principal
        for method, path, body in (
            ("post", f"{BASE}/accounts/{account}/proposals", {"symbol": "1101.TW"}),
            ("get", f"{BASE}/accounts/{account}/proposals", None),
            ("get", f"{BASE}/accounts/{account}/proposals/{uuid4()}", None),
            ("post", f"{BASE}/accounts/{account}/proposals/{uuid4()}/reject", {"reason": None}),
        ):
            response = client.request(method, path, json=body)
            assert response.status_code in (401, 403), (principal.kind, path, response.status_code)


def test_list_get_reject_and_privacy(client) -> None:
    account = _account(client)
    first = _create(client, account).json()
    second = _create(client, account).json()
    listed = client.get(f"{BASE}/accounts/{account}/proposals").json()
    assert [p["id"] for p in listed["proposals"]] == [second["id"], first["id"]]
    assert listed["has_more"] is False
    only = client.get(f"{BASE}/accounts/{account}/proposals?status=superseded").json()
    assert [p["id"] for p in only["proposals"]] == [first["id"]]
    assert client.get(f"{BASE}/accounts/{account}/proposals?status=bogus").status_code == 422
    assert client.get(f"{BASE}/accounts/{account}/proposals?limit=0").status_code == 422
    page = client.get(f"{BASE}/accounts/{account}/proposals?limit=1&offset=1").json()
    assert [p["id"] for p in page["proposals"]] == [first["id"]] and page["has_more"] is False

    got = client.get(f"{BASE}/accounts/{account}/proposals/{second['id']}")
    assert got.status_code == 200 and got.json()["id"] == second["id"]
    assert client.get(f"{BASE}/accounts/{account}/proposals/{uuid4()}").status_code == 404

    url = f"{BASE}/accounts/{account}/proposals/{second['id']}/reject"
    rejected = client.post(url, json={"reason": "不想買"})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected" and rejected.json()["reject_reason"] == "不想買"
    assert client.post(url, json={"reason": None}).status_code == 409
    assert (
        client.post(
            f"{BASE}/accounts/{account}/proposals/{first['id']}/reject", json={"reason": None}
        ).status_code
        == 409
    )  # superseded
    assert client.post(url, json={"reason": "x" * 201}).status_code == 422

    Who.principal = Principal("user", user=_user(YOU))
    assert client.get(f"{BASE}/accounts/{account}/proposals").status_code == 404
    assert client.get(f"{BASE}/accounts/{account}/proposals/{second['id']}").status_code == 404
    assert client.post(url, json={"reason": None}).status_code == 404


def test_accepting_through_the_order_route(client) -> None:
    account = _account(client)
    proposal = _create(client, account).json()
    order = {
        "symbol": "1101.TW",
        "side": "buy",
        "order_type": "limit",
        "limit_price": 100,
        "lots": 1,
        "proposal_id": proposal["id"],
    }
    orders_url = f"{BASE}/accounts/{account}/orders"
    wrong = client.post(orders_url, json={**order, "side": "sell"})
    assert wrong.status_code == 409
    preview = client.post(orders_url + "?preview=true", json=order)
    assert preview.status_code == 200 and preview.json()["ok"] is True
    after_preview = client.get(f"{BASE}/accounts/{account}/proposals/{proposal['id']}").json()
    assert after_preview["status"] == "pending"
    assert client.get(orders_url).json()["orders"] == []

    placed = client.post(orders_url, json=order)
    assert placed.status_code == 200, placed.text
    assert placed.json()["source"] == "ai" and placed.json()["status"] == "working"
    accepted = client.get(f"{BASE}/accounts/{account}/proposals/{proposal['id']}").json()
    assert accepted["status"] == "accepted" and accepted["order_id"] == placed.json()["id"]
    assert accepted["decided_at"] is not None
    assert client.post(orders_url, json=order).status_code == 409
    assert client.post(orders_url, json={**order, "proposal_id": "not-a-uuid"}).status_code == 422


def test_orders_without_a_proposal_stay_manual(client) -> None:
    account = _account(client)
    manual = client.post(
        f"{BASE}/accounts/{account}/orders",
        json={
            "symbol": "1101.TW",
            "side": "buy",
            "order_type": "limit",
            "limit_price": 100,
            "lots": 1,
        },
    )
    assert manual.json()["source"] == "manual"


def test_rules_expose_the_proposal_limits(client) -> None:
    rules = client.get(f"{BASE}/rules").json()
    assert rules["proposals"] == {"enabled": True, "max_position_pct": 0.2, "max_risk_pct": 0.02}
    assert rules["board_lot"] == 1000  # existing keys are untouched


def test_unused_fixture_helpers_import() -> None:
    assert FixedContext is not None
