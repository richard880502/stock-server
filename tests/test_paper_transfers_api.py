"""Paper-account transfer routes: shapes, status codes, the changed summary fields."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from paper_support import FRI, at, market_repo, quote

from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.adapters.rest.security import Principal, require_principal
from quant_signal.application import paper_trading
from quant_signal.domain.models import User

ME = uuid4()


def _user() -> User:
    return User(id=ME, google_sub=f"g-{ME}", email=f"{ME}@x.com", created_at=datetime.now(UTC))


@pytest.fixture
def client(monkeypatch):
    import asyncio

    repo = asyncio.run(market_repo())
    repo.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    now = {"value": at(FRI, 10, 0)}
    real = paper_trading.PaperService.__init__

    def pinned(self, *args, **kwargs):
        kwargs.setdefault("clock", lambda: now["value"])
        real(self, *args, **kwargs)

    async def override():
        yield repo

    monkeypatch.setattr(paper_trading.PaperService, "__init__", pinned)
    app.dependency_overrides[get_repository] = override
    app.dependency_overrides[require_principal] = lambda: Principal("user", user=_user())
    with TestClient(app) as c:
        c.now = now  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


def _account(client) -> dict:
    response = client.post("/api/v1/paper/accounts", json={"name": "主帳戶"})
    assert response.status_code == 201, response.text
    return response.json()


def _post(client, account, **body):
    body.setdefault("request_id", str(uuid4()))
    return client.post(f"/api/v1/paper/accounts/{account['id']}/transfers", json=body)


def test_deposit_returns_the_updated_summary_with_the_new_fields(client) -> None:
    account = _account(client)
    assert account["contributed_capital"] == 1_000_000
    response = _post(client, account, direction="in", amount=500_000, note="加碼")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["cash"], body["equity"], body["contributed_capital"]) == (1_500_000,) * 3
    assert (body["total_pnl"], body["total_return_pct"], body["day_pnl"]) == (0, 0.0, 0)
    detail = client.get(f"/api/v1/paper/accounts/{account['id']}").json()
    assert detail["contributed_capital"] == 1_500_000 and detail["initial_cash"] == 1_000_000


def test_listing_envelope_and_paging(client) -> None:
    account = _account(client)
    for i in range(3):
        client.now["value"] = at(FRI, 10, i)
        _post(client, account, direction="out" if i == 1 else "in", amount=1_000 * (i + 1))
    url = f"/api/v1/paper/accounts/{account['id']}/transfers"
    page = client.get(url, params={"limit": 2}).json()
    assert page["has_more"] is True
    assert [(t["direction"], t["amount"]) for t in page["transfers"]] == [
        ("in", 3_000),
        ("out", 2_000),
    ]
    assert set(page["transfers"][0]) == {"id", "direction", "amount", "at", "note"}
    rest = client.get(url, params={"limit": 2, "offset": 2}).json()
    assert rest["has_more"] is False and len(rest["transfers"]) == 1
    assert client.get(url, params={"limit": 0}).status_code == 422


def test_validation_and_conflicts_map_to_422_and_409(client) -> None:
    account = _account(client)
    assert _post(client, account, direction="in", amount=999).status_code == 422
    assert _post(client, account, direction="in", amount=100_000_001).status_code == 422
    assert _post(client, account, direction="up", amount=5_000).status_code == 422
    assert _post(client, account, direction="in", amount=5_000, note="x" * 61).status_code == 422
    url = f"/api/v1/paper/accounts/{account['id']}/transfers"
    assert client.post(url, json={"direction": "in", "amount": 5_000}).status_code == 422
    over = _post(client, account, direction="out", amount=1_000_001)
    assert over.status_code == 409 and "可用資金" in over.json()["detail"]
    for _ in range(20):
        assert _post(client, account, direction="in", amount=1_000).status_code == 200
    capped = _post(client, account, direction="in", amount=1_000)
    assert capped.status_code == 409 and "20" in capped.json()["detail"]


def test_a_repeated_request_id_is_answered_once(client) -> None:
    account = _account(client)
    rid = str(uuid4())
    first = _post(client, account, direction="in", amount=40_000, request_id=rid)
    again = _post(client, account, direction="in", amount=40_000, request_id=rid)
    assert first.json()["cash"] == again.json()["cash"] == 1_040_000
    listing = client.get(f"/api/v1/paper/accounts/{account['id']}/transfers").json()
    assert len(listing["transfers"]) == 1


def test_other_users_and_missing_accounts_are_404(client) -> None:
    response = client.post(
        f"/api/v1/paper/accounts/{uuid4()}/transfers",
        json={"direction": "in", "amount": 5_000, "request_id": str(uuid4())},
    )
    assert response.status_code == 404
    assert client.get(f"/api/v1/paper/accounts/{uuid4()}/transfers").status_code == 404


def test_reset_with_a_body_without_one_and_with_a_bad_one(client) -> None:
    account = _account(client)
    url = f"/api/v1/paper/accounts/{account['id']}/reset"
    assert client.post(url, json={"initial_cash": 99_999}).status_code == 422
    assert client.post(url, json={"initial_cash": 100_000_001}).status_code == 422
    assert client.post(url, json={"bogus": 1}).status_code == 422
    plain = client.post(url)
    assert plain.status_code == 200 and plain.json()["initial_cash"] == 1_000_000
    bigger = client.post(url, json={"initial_cash": 3_000_000}).json()
    assert (bigger["initial_cash"], bigger["cash"], bigger["contributed_capital"]) == (
        3_000_000,
    ) * 3


def test_equity_points_carry_net_transfer_and_twr_index(client) -> None:
    account = _account(client)
    _post(client, account, direction="in", amount=1_000_000)
    points = client.get(f"/api/v1/paper/accounts/{account['id']}/equity").json()["points"]
    assert points[-1]["live"] is True
    assert points[-1]["net_transfer"] == 1_000_000 and points[-1]["twr_index"] == 100.0
    assert points[-1]["equity"] == 2_000_000 and points[-1]["post_close_transfer"] == 0
    summary = client.get(f"/api/v1/paper/accounts/{account['id']}").json()
    assert (summary["day_net_transfer"], summary["post_close_transfer"]) == (1_000_000, 0)


def test_a_reused_request_id_with_another_body_is_409_and_a_retry_replays(client) -> None:
    account = _account(client)
    rid = str(uuid4())
    first = _post(client, account, direction="in", amount=100_000, request_id=rid)
    assert first.status_code == 200 and first.json()["cash"] == 1_100_000
    # the response was lost; the user edits the amount and resubmits under the same id
    edited = _post(client, account, direction="in", amount=500_000, request_id=rid)
    assert edited.status_code == 409
    assert "入金 NT$100,000" in edited.json()["detail"]
    assert (
        _post(client, account, direction="out", amount=100_000, request_id=rid).status_code == 409
    )
    same = _post(client, account, direction="in", amount=100_000, request_id=rid)
    assert same.status_code == 200 and same.json()["cash"] == 1_100_000
    listed = client.get(f"/api/v1/paper/accounts/{account['id']}/transfers").json()
    assert [t["amount"] for t in listed["transfers"]] == [100_000]


def test_the_note_is_stripped_before_its_length_is_checked(client) -> None:
    account = _account(client)
    padded = "  " + "備" * 60 + "   "
    assert _post(client, account, direction="in", amount=1_000, note=padded).status_code == 200
    assert _post(client, account, direction="in", amount=1_000, note="x" * 61).status_code == 422
    listed = client.get(f"/api/v1/paper/accounts/{account['id']}/transfers").json()
    assert listed["transfers"][-1]["note"] == "備" * 60


def test_rules_mention_transfers(client) -> None:
    notes = client.get("/api/v1/paper/rules").json()["notes"]
    assert any("入金" in n and "時間加權" in n for n in notes)
