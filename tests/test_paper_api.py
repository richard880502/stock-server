"""Paper-trading REST routes: auth, isolation, validation, preview vs create, cancel."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from paper_support import FRI, MON, THU, at, market_repo, quote

from quant_signal.adapters.rest import paper as paper_routes
from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.adapters.rest.security import Principal, require_principal
from quant_signal.application import paper_trading
from quant_signal.application.paper_jobs import ledger_entries
from quant_signal.domain.models import User
from quant_signal.domain.paper import PaperFillRecord, PaperOrderRecord
from quant_signal.infrastructure.memory import MemoryQuantRepository

ME, YOU = uuid4(), uuid4()


def _user(user_id: UUID) -> User:
    return User(
        id=user_id,
        google_sub=f"g-{user_id}",
        email=f"{user_id}@x.com",
        created_at=datetime.now(UTC),
    )


class Who:
    principal: Principal = Principal("user", user=_user(ME))


@pytest.fixture
async def repo() -> MemoryQuantRepository:
    repository = await market_repo()
    repository.quotes_latest["1101.TW"] = quote("1101.TW", 100, at(FRI, 9, 59))
    return repository


@pytest.fixture
def client(repo, monkeypatch):
    Who.principal = Principal("user", user=_user(ME))

    async def override():
        yield repo

    # Pin "now" to a Friday morning session so previews are deterministic.
    real = paper_trading.PaperService.__init__

    def pinned(self, *args, **kwargs):
        kwargs.setdefault("clock", lambda: at(FRI, 10, 0))
        real(self, *args, **kwargs)

    monkeypatch.setattr(paper_trading.PaperService, "__init__", pinned)
    app.dependency_overrides[get_repository] = override
    app.dependency_overrides[require_principal] = lambda: Who.principal
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _account(client, name="主帳戶", **extra) -> dict:
    response = client.post("/api/v1/paper/accounts", json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _order(side="buy", order_type="market", lots=1, **extra) -> dict:
    return {"symbol": "1101.TW", "side": side, "order_type": order_type, "lots": lots, **extra}


def test_requires_a_signed_in_user(client) -> None:
    for principal in (Principal("anonymous"), Principal("api_key")):
        Who.principal = principal
        for method, path in (
            ("get", "/api/v1/paper/accounts"),
            ("post", "/api/v1/paper/accounts"),
            ("get", "/api/v1/paper/rules"),
            ("get", f"/api/v1/paper/accounts/{uuid4()}"),
        ):
            response = client.request(
                method, path, json={"name": "x"} if method == "post" else None
            )
            assert response.status_code == 403, (principal.kind, path)
            assert "登入" in response.json()["detail"]


def test_account_crud_defaults_and_validation(client) -> None:
    created = _account(client)
    assert created["initial_cash"] == 1_000_000 and created["cash"] == 1_000_000
    assert created["commission_discount"] == 1.0 and created["allow_day_trade"] is False
    assert created["equity"] == 1_000_000 and created["positions_count"] == 0
    listed = client.get("/api/v1/paper/accounts").json()
    assert listed["max_accounts"] == 3 and [a["name"] for a in listed["accounts"]] == ["主帳戶"]
    detail = client.get(f"/api/v1/paper/accounts/{created['id']}").json()
    assert detail["positions"] == [] and detail["benchmark"] is None
    patched = client.patch(
        f"/api/v1/paper/accounts/{created['id']}",
        json={"name": "短線", "commission_discount": 0.6, "allow_day_trade": True},
    ).json()
    assert patched["name"] == "短線" and patched["commission_discount"] == 0.6
    assert patched["allow_day_trade"] is True
    for bad in (
        {"name": "x", "initial_cash": 99_999},
        {"name": "x", "initial_cash": 100_000_001},
        {"name": "x", "commission_discount": 0.05},
        {"name": "x", "commission_discount": 1.5},
        {"name": "  "},
        {"name": ""},
    ):
        assert client.post("/api/v1/paper/accounts", json=bad).status_code == 422, bad
    deleted = client.delete(f"/api/v1/paper/accounts/{created['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/paper/accounts/{created['id']}").status_code == 404


def test_huge_and_malformed_inputs_are_422_with_chinese_messages(client) -> None:
    acct = _account(client)
    base = f"/api/v1/paper/accounts/{acct['id']}/orders"
    cases = [
        (_order(lots=3_000_000_000), "張數"),
        (_order(lots=0), "張數"),
        (_order(lots=10_001), "張數"),
        (_order(order_type="limit", limit_price=1e11), "限價"),
        (_order(order_type="limit", limit_price=100_000), "限價"),
        (_order(order_type="limit", limit_price=0), "限價"),
        (_order(order_type="limit", limit_price=-1), "限價"),
        (_order(order_type="limit", limit_price=10.123), "2 位小數"),
    ]
    for body, needle in cases:
        for url in (base, base + "?preview=true"):
            response = client.post(url, json=body)
            assert response.status_code == 422, (body, response.text)
            assert needle in response.text, (body, response.text)
    assert client.get(base).json() == {"orders": [], "has_more": False}  # nothing was stored
    edge = client.post(base + "?preview=true", json=_order(lots=10_000))
    assert edge.status_code == 200 and edge.json()["ok"] is False  # funds, not a 500
    for bad in (
        {"name": "y", "initial_cash": 10**15},
        {"name": "y", "commission_discount": 0.0999},
        {"name": "y", "commission_discount": 1.0001},
    ):
        response = client.post("/api/v1/paper/accounts", json=bad)
        assert response.status_code == 422, bad
    patch = client.patch(f"/api/v1/paper/accounts/{acct['id']}", json={"commission_discount": 0.05})
    assert patch.status_code == 422 and "手續費折數" in patch.text


def test_raising_the_discount_without_the_cash_is_a_409(client) -> None:
    acct = _account(client, initial_cash=200_200, commission_discount=0.6)
    url = f"/api/v1/paper/accounts/{acct['id']}"
    for _ in range(2):
        placed = client.post(url + "/orders", json=_order(order_type="limit", limit_price=100))
        assert placed.status_code == 200 and placed.json()["reserved_cash"] == 100_085
    refused = client.patch(url, json={"commission_discount": 1.0})
    assert refused.status_code == 409 and "可用資金不足" in refused.json()["detail"]
    assert client.get(url).json()["commission_discount"] == 0.6


def test_etf_without_a_price_limit_previews_null_limits(client, repo) -> None:
    repo.quotes_latest["0050.TW"] = quote("0050.TW", 150, at(FRI, 9, 59), reference=150)
    acct = _account(client)
    url = f"/api/v1/paper/accounts/{acct['id']}/orders?preview=true"
    preview = client.post(url, json={**_order(), "symbol": "0050.TW"}).json()
    assert preview["limit_up"] is None and preview["limit_down"] is None
    assert preview["ok"] is True and preview["reserve"] == 165_235


def test_max_accounts_and_duplicate_names(client) -> None:
    for name in ("A", "B", "C"):
        _account(client, name)
    full = client.post("/api/v1/paper/accounts", json={"name": "D"})
    assert full.status_code == 409 and "最多 3" in full.json()["detail"]
    Who.principal = Principal("user", user=_user(YOU))
    _account(client, "A")  # another user may reuse a name
    dup = client.post("/api/v1/paper/accounts", json={"name": "A"})
    assert dup.status_code == 409 and "已有名為" in dup.json()["detail"]


def test_users_never_see_each_others_accounts(client) -> None:
    mine = _account(client)
    order = client.post(f"/api/v1/paper/accounts/{mine['id']}/orders", json=_order()).json()
    Who.principal = Principal("user", user=_user(YOU))
    base = f"/api/v1/paper/accounts/{mine['id']}"
    assert client.get("/api/v1/paper/accounts").json()["accounts"] == []
    for method, path, body in (
        ("get", base, None),
        ("patch", base, {"name": "hack"}),
        ("post", base + "/reset", {}),
        ("delete", base, None),
        ("get", base + "/orders", None),
        ("post", base + "/orders", _order()),
        ("post", base + "/orders?preview=true", _order()),
        ("delete", f"{base}/orders/{order['id']}", None),
        ("get", base + "/fills", None),
        ("get", base + "/equity", None),
    ):
        assert client.request(method, path, json=body).status_code == 404, (method, path)
    Who.principal = Principal("user", user=_user(ME))
    assert client.get(f"/api/v1/paper/accounts/{mine['id']}").status_code == 200
    # an order id from another account cannot be cancelled through your own account
    other = _account(client, "other")
    wrong = client.delete(f"/api/v1/paper/accounts/{other['id']}/orders/{order['id']}")
    assert wrong.status_code == 404


def test_preview_does_not_write_and_create_does(client) -> None:
    acct = _account(client)
    url = f"/api/v1/paper/accounts/{acct['id']}/orders"
    preview = client.post(url + "?preview=true", json=_order(lots=2)).json()
    assert preview["ok"] is True and preview["shares"] == 2000
    assert preview["reference_price"] == 100.0 and preview["last_price"] == 100.0
    assert (preview["limit_up"], preview["limit_down"], preview["tick"]) == (110.0, 90.0, 0.5)
    assert preview["estimated_price"] == 100.5 and preview["estimated_amount"] == 201_000
    assert preview["estimated_commission"] == 286 and preview["estimated_total"] == 201_286
    assert preview["estimated_tax"] == 0 and preview["reserve"] == 220_313
    assert preview["available_cash_after"] == 1_000_000 - 220_313
    assert (
        preview["session_date"] == "2026-11-27" and preview["will_queue_for_next_session"] is False
    )
    assert client.get(url).json() == {"orders": [], "has_more": False}
    created = client.post(url + "?preview=false", json=_order(lots=2, note="試單"))
    body = created.json()
    assert created.status_code == 200 or created.status_code == 201
    assert body["status"] == "working" and body["reserved_cash"] == 220_313
    assert body["note"] == "試單" and body["fill"] is None and body["source"] == "manual"
    listed = client.get(url + "?status=working").json()
    assert [o["id"] for o in listed["orders"]] == [body["id"]]
    assert client.get(url + "?status=done").json()["orders"] == []
    assert client.get(f"/api/v1/paper/accounts/{acct['id']}").json()["working_orders"] == 1


def test_sell_preview_reports_net_proceeds_and_zero_reserve(client, repo) -> None:
    acct = _account(client)
    account_id = UUID(acct["id"])

    async def give_shares():
        order = PaperOrderRecord(
            account_id=account_id,
            symbol="1101.TW",
            name="台泥",
            side="buy",
            order_type="market",
            lots=2,
            session_date=THU,
            created_at=at(THU, 9),
            updated_at=at(THU, 9),
        )
        await repo.add_paper_order(order)
        fill = PaperFillRecord(
            order_id=order.id,
            account_id=account_id,
            symbol="1101.TW",
            side="buy",
            shares=2000,
            price=Decimal("100"),
            amount=200_000,
            commission=285,
            tax=0,
            filled_at=at(THU, 9, 5),
            price_source="tick",
            price_time=at(THU, 9, 5),
        )
        await repo.apply_paper_fill(fill, ledger_entries(fill, at(THU, 9, 5)), now=at(THU, 9, 5))

    asyncio.run(give_shares())
    preview = client.post(
        f"/api/v1/paper/accounts/{acct['id']}/orders?preview=true", json=_order("sell")
    ).json()
    # sell 1,000 at 99.9: amount 99,900, commission 142, tax 299 -> net 99,459
    assert preview["estimated_price"] == 99.9 and preview["estimated_amount"] == 99_900
    assert (preview["estimated_commission"], preview["estimated_tax"]) == (142, 299)
    assert preview["estimated_total"] == 99_459 and preview["reserve"] == 0
    assert preview["available_cash_after"] == 1_000_000 - 200_285 + 99_459


def test_preview_returns_market_facts_even_when_the_order_is_invalid(client) -> None:
    acct = _account(client)
    url = f"/api/v1/paper/accounts/{acct['id']}/orders?preview=true"
    broke = client.post(url, json=_order(lots=500)).json()
    assert broke["ok"] is False and "資金不足" in broke["reject_reason"] and broke["tick"] == 0.5
    assert (broke["reference_price"], broke["last_price"], broke["limit_up"]) == (
        100.0,
        100.0,
        110.0,
    )
    assert broke["limit_down"] == 90.0
    unknown = client.post(url, json={**_order(), "symbol": "9999.TW"}).json()
    assert unknown["ok"] is False and "找不到股票" in unknown["reject_reason"]


def test_preview_works_outside_the_session(client, monkeypatch) -> None:
    acct = _account(client)
    real = paper_trading.PaperService.__init__

    def night(self, *args, **kwargs):
        kwargs["clock"] = lambda: at(FRI, 20, 0)
        real(self, *args, **kwargs)

    monkeypatch.setattr(paper_trading.PaperService, "__init__", night)
    preview = client.post(
        f"/api/v1/paper/accounts/{acct['id']}/orders?preview=true", json=_order()
    ).json()
    assert preview["will_queue_for_next_session"] is True
    assert preview["session_date"] == MON.isoformat() and preview["price_delayed"] is False
    assert preview["reference_price"] == 100.0 and any(
        "下一" in w or "排入" in w for w in preview["warnings"]
    )


def test_rejected_order_is_stored_and_listed(client) -> None:
    acct = _account(client)
    url = f"/api/v1/paper/accounts/{acct['id']}/orders"
    rejected = client.post(url, json=_order(order_type="limit", limit_price=100.3)).json()
    assert rejected["status"] == "rejected" and "升降單位" in rejected["reject_reason"]
    assert client.get(url + "?status=done").json()["orders"][0]["id"] == rejected["id"]
    assert client.post(url, json=_order(order_type="limit")).status_code == 422
    assert client.post(url, json=_order(limit_price=100)).status_code == 422
    assert client.post(url, json={**_order(), "side": "short"}).status_code == 422
    assert client.post(url, json={**_order(), "symbol": "$$"}).status_code == 422
    assert client.post(url, json={**_order(), "extra": 1}).status_code == 422


def test_cancel_and_conflicts(client) -> None:
    acct = _account(client)
    url = f"/api/v1/paper/accounts/{acct['id']}/orders"
    order = client.post(url, json=_order(order_type="limit", limit_price=100)).json()
    cancelled = client.delete(f"{url}/{order['id']}")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    again = client.delete(f"{url}/{order['id']}")
    assert again.status_code == 409 and "無法取消" in again.json()["detail"]
    assert client.delete(f"{url}/{uuid4()}").status_code == 404
    assert client.get(f"/api/v1/paper/accounts/{acct['id']}").json()["available_cash"] == 1_000_000


def test_reset_accepts_empty_body_and_reports_fresh_account(client) -> None:
    acct = _account(client, initial_cash=500_000)
    url = f"/api/v1/paper/accounts/{acct['id']}"
    client.post(url + "/orders", json=_order(order_type="limit", limit_price=100))
    for body in ({}, None):
        reset = client.post(url + "/reset", json=body)
        assert reset.status_code == 200, reset.text
    summary = reset.json()
    assert summary["cash"] == 500_000 and summary["working_orders"] == 0
    assert client.get(url + "/orders").json()["orders"] == []
    assert len(client.get(url + "/orders?include_archived=true").json()["orders"]) == 1


def test_list_pagination_fills_and_equity(client) -> None:
    acct = _account(client)
    base = f"/api/v1/paper/accounts/{acct['id']}"
    for _ in range(3):
        client.post(base + "/orders", json=_order(order_type="limit", limit_price=100))
    page = client.get(base + "/orders?limit=2").json()
    assert len(page["orders"]) == 2 and page["has_more"] is True
    assert client.get(base + "/orders?limit=2&offset=2").json()["has_more"] is False
    assert client.get(base + "/orders?limit=0").status_code == 422
    assert client.get(base + "/fills").json() == {"fills": [], "has_more": False}
    curve = client.get(base + "/equity?days=365").json()["points"]
    assert len(curve) == 1 and curve[0]["live"] is True and curve[0]["equity"] == 1_000_000
    assert client.get(base + "/equity?days=100000").status_code == 200  # clamped, not 422
    assert client.get(base + "/equity?days=0").status_code == 422


def test_rules_are_derived_from_the_trading_module(client) -> None:
    rules = client.get("/api/v1/paper/rules").json()
    assert rules["board_lot"] == 1000 and rules["commission_rate"] == 0.001425
    assert rules["min_commission"] == 20 and rules["stock_tax_rate"] == 0.003
    assert rules["etf_tax_rate"] == 0.001 and rules["day_trade_tax_rate"] == 0.0015
    assert rules["price_limit_pct"] == 0.1 and rules["slippage_ticks"] == 1
    assert rules["tick_bands"][0] == {"min": 0.0, "max": 10.0, "tick": 0.01}
    assert rules["tick_bands"][-1] == {"min": 1000.0, "max": None, "tick": 5.0}
    assert rules["etf_tick_bands"][0]["tick"] == 0.01
    assert rules["session"] == {"open": "09:00", "close": "13:30", "timezone": "Asia/Taipei"}
    assert rules["max_accounts"] == 3 and all(isinstance(n, str) for n in rules["notes"])


def test_routes_off_when_disabled() -> None:
    from fastapi import FastAPI

    from quant_signal.settings import get_settings

    bare = FastAPI()
    paper_routes.install_paper(
        bare, get_settings().model_copy(update={"paper_trading_enabled": False}), get_repository
    )
    assert TestClient(bare).get("/api/v1/paper/rules").status_code == 404
