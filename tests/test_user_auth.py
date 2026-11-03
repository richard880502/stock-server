from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import quant_signal.adapters.rest.main as rest_main
from quant_signal.adapters.rest.main import app, get_repository
from quant_signal.application import user_auth
from quant_signal.application.user_auth import (
    GoogleLoginError,
    email_allowed,
    new_pkce,
    parse_id_token,
    safe_next,
    sign,
    start_login,
    unsign,
)
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.settings import Settings, get_settings

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
CLIENT_ID = "client-123.apps.googleusercontent.com"


def _settings(**overrides) -> Settings:
    base = {
        "google_client_id": CLIENT_ID,
        "google_client_secret": "shh",
        "google_redirect_uri": "http://127.0.0.1:18100/api/v1/auth/google/callback",
        "session_secret": "x" * 40,
    }
    return Settings(_env_file=None, **{**base, **overrides})


def _id_token(**claims) -> str:
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": (NOW + timedelta(minutes=5)).timestamp(),
        "nonce": "n-1",
        "email_verified": True,
        "sub": "1234567890",
        "email": "Richard@Gmail.com",
        "name": "Richard",
        "picture": "https://example.com/p.png",
        **claims,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{body}.sig"


# --- pure helpers -----------------------------------------------------------


def test_signed_state_round_trips_and_rejects_tampering_and_expiry() -> None:
    settings = _settings()
    token = sign({"state": "abc", "exp": (NOW + timedelta(minutes=1)).timestamp()}, settings)

    assert unsign(token, settings, now=NOW)["state"] == "abc"
    assert unsign(token, settings, now=NOW + timedelta(minutes=2)) is None
    body, _, sig = token.partition(".")
    assert unsign(f"{body}x.{sig}", settings, now=NOW) is None
    assert unsign(token, _settings(session_secret="y" * 40), now=NOW) is None
    assert unsign("garbage", settings, now=NOW) is None and unsign(None, settings, now=NOW) is None


def test_pkce_challenge_matches_the_verifier() -> None:
    import hashlib

    verifier, challenge = new_pkce()
    digest = hashlib.sha256(verifier.encode()).digest()
    assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert len(verifier) >= 43


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/market?x=1", "/market?x=1"),
        ("//evil.example", "/"),
        ("https://evil.example", "/"),
        ("/\\evil", "/"),
        ("", "/"),
        (None, "/"),
    ],
)
def test_next_path_must_stay_on_site(raw, expected) -> None:
    assert safe_next(raw) == expected


def test_authorize_url_carries_pkce_state_and_nonce() -> None:
    settings = _settings()
    url, cookie = start_login(settings, next_path="/market", now=NOW)
    query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    saved = unsign(cookie, settings, now=NOW)

    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert query["client_id"] == CLIENT_ID and query["code_challenge_method"] == "S256"
    assert query["scope"] == "openid email profile"
    assert query["state"] == saved["state"] and query["nonce"] == saved["nonce"]
    assert saved["next"] == "/market" and saved["verifier"]


def test_valid_id_token_yields_a_lowercased_identity() -> None:
    who = parse_id_token(_id_token(), client_id=CLIENT_ID, nonce="n-1", now=NOW)

    assert who.email == "richard@gmail.com" and who.sub == "1234567890"


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://evil.example"},
        {"aud": "someone-else"},
        {"exp": (NOW - timedelta(seconds=1)).timestamp()},
        {"nonce": "other"},
        {"email_verified": False},
        {"email": ""},
        {"sub": ""},
    ],
)
def test_bad_id_tokens_are_rejected(claims) -> None:
    with pytest.raises(GoogleLoginError):
        parse_id_token(_id_token(**claims), client_id=CLIENT_ID, nonce="n-1", now=NOW)


def test_audience_list_and_alt_issuer_are_accepted() -> None:
    token = _id_token(aud=["x", CLIENT_ID], iss="accounts.google.com")
    assert parse_id_token(token, client_id=CLIENT_ID, nonce="n-1", now=NOW).sub


def test_allow_lists() -> None:
    assert email_allowed("anyone@x.com", _settings())
    only = _settings(auth_allowed_emails="a@x.com, B@y.com")
    assert email_allowed("b@y.com", only) and not email_allowed("c@y.com", only)
    domain = _settings(auth_allowed_email_domains="@corp.com")
    assert email_allowed("Z@Corp.com", domain) and not email_allowed("z@other.com", domain)


# --- HTTP flow --------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    repo = MemoryQuantRepository()
    settings = _settings(api_auth_enabled=True, quota_llm_per_day=2)

    async def override():
        yield repo

    from quant_signal.adapters.rest import security

    app.dependency_overrides[get_repository] = override
    app.dependency_overrides[security._get_repository] = override
    app.dependency_overrides[get_settings] = lambda: settings
    for module in (rest_main, security):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(user_auth, "utcnow", lambda: NOW)
    monkeypatch.setattr(rest_main, "utcnow", lambda: NOW)
    monkeypatch.setattr(security, "utcnow", lambda: NOW)
    with TestClient(app, follow_redirects=False, base_url="http://127.0.0.1:18100") as c:
        c.repo, c.settings = repo, settings  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


def _fake_google(monkeypatch, *, nonce_from_cookie: dict, **claims) -> None:
    async def exchange(settings, code, verifier, *, client=None):
        assert code == "auth-code" and verifier
        return _id_token(nonce=nonce_from_cookie["nonce"], **claims)

    monkeypatch.setattr(rest_main, "exchange_code", exchange)


def _begin(client) -> dict:
    response = client.get("/api/v1/auth/google/login", params={"next": "/market"})
    assert response.status_code == 303
    query = {k: v[0] for k, v in parse_qs(urlparse(response.headers["location"]).query).items()}
    saved = unsign(client.cookies.get(user_auth.STATE_COOKIE), client.settings, now=NOW)
    return {**query, "nonce": saved["nonce"]}


def test_config_reports_what_is_available(client) -> None:
    assert client.get("/api/v1/auth/config").json() == {"google": True, "password": False}


def test_google_login_is_unavailable_until_configured(client, monkeypatch) -> None:
    unconfigured = _settings(google_client_id="")
    monkeypatch.setattr(rest_main, "get_settings", lambda: unconfigured)

    assert client.get("/api/v1/auth/google/login").status_code == 503


def test_full_login_creates_a_user_a_session_and_unlocks_the_api(client, monkeypatch) -> None:
    started = _begin(client)
    _fake_google(monkeypatch, nonce_from_cookie=started)

    response = client.get(
        "/api/v1/auth/google/callback", params={"code": "auth-code", "state": started["state"]}
    )

    assert response.status_code == 303 and response.headers["location"].endswith("/market")
    cookie = response.headers["set-cookie"]
    assert "qs_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Secure" not in cookie  # plain-http local redirect URI
    (user,) = client.repo.users.values()
    assert user.email == "richard@gmail.com" and user.name == "Richard"
    # Only a hash of the token is stored.
    assert all(len(h) == 64 for h in client.repo.user_sessions)

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "richard@gmail.com"
    assert client.get("/api/v1/market/data-status").status_code == 200


def test_the_api_is_locked_without_a_session(client) -> None:
    assert client.get("/api/v1/market/data-status").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401


def test_callback_rejects_a_bad_or_missing_state(client, monkeypatch) -> None:
    started = _begin(client)
    _fake_google(monkeypatch, nonce_from_cookie=started)

    wrong = client.get("/api/v1/auth/google/callback", params={"code": "auth-code", "state": "x"})
    client.cookies.clear()
    missing = client.get(
        "/api/v1/auth/google/callback", params={"code": "auth-code", "state": started["state"]}
    )

    assert "login_error=state" in wrong.headers["location"]
    assert "login_error=state" in missing.headers["location"]
    assert client.repo.users == {}


def test_user_cancelling_at_google_is_reported(client) -> None:
    response = client.get("/api/v1/auth/google/callback", params={"error": "access_denied"})

    assert "login_error=access_denied" in response.headers["location"]


def test_disallowed_email_gets_no_session(client, monkeypatch) -> None:
    client.settings.auth_allowed_emails = "someone-else@x.com"
    started = _begin(client)
    _fake_google(monkeypatch, nonce_from_cookie=started)

    response = client.get(
        "/api/v1/auth/google/callback", params={"code": "auth-code", "state": started["state"]}
    )

    assert "login_error=email_not_allowed" in response.headers["location"]
    assert client.repo.users == {} and client.repo.user_sessions == {}


def test_token_problems_become_a_login_error(client, monkeypatch) -> None:
    started = _begin(client)
    _fake_google(monkeypatch, nonce_from_cookie=started, email_verified=False)

    response = client.get(
        "/api/v1/auth/google/callback", params={"code": "auth-code", "state": started["state"]}
    )

    assert "login_error=email_unverified" in response.headers["location"]


def test_logout_revokes_the_session(client, monkeypatch) -> None:
    started = _begin(client)
    _fake_google(monkeypatch, nonce_from_cookie=started)
    client.get(
        "/api/v1/auth/google/callback", params={"code": "auth-code", "state": started["state"]}
    )
    token = client.cookies.get("qs_session")

    assert client.post("/api/v1/auth/logout").status_code == 200
    client.cookies.set("qs_session", token)  # a stolen copy must be dead too

    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.repo.user_sessions == {}


def test_an_expired_session_is_not_accepted(client, monkeypatch) -> None:
    started = _begin(client)
    _fake_google(monkeypatch, nonce_from_cookie=started)
    client.get(
        "/api/v1/auth/google/callback", params={"code": "auth-code", "state": started["state"]}
    )
    from quant_signal.adapters.rest import security

    monkeypatch.setattr(security, "utcnow", lambda: NOW + timedelta(days=31))

    assert client.get("/api/v1/auth/me").status_code == 401


def test_api_keys_still_work_and_are_exempt_from_quotas(client) -> None:
    import asyncio

    from quant_signal.application.auth import ApiKeyService

    raw, _ = asyncio.run(ApiKeyService(client.repo).create("ci"))
    headers = {"Authorization": f"Bearer {raw}"}

    assert client.get("/api/v1/market/data-status", headers=headers).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401  # not a person


def test_daily_quota_blocks_the_third_llm_call_with_a_retry_hint(client, monkeypatch) -> None:
    started = _begin(client)
    _fake_google(monkeypatch, nonce_from_cookie=started)
    client.get(
        "/api/v1/auth/google/callback", params={"code": "auth-code", "state": started["state"]}
    )
    body = {"symbol": "2330.TW", "as_of": "2026-11-03", "market": "TW"}

    statuses = [client.post("/api/v1/llm-analyses", json=body).status_code for _ in range(3)]

    # The first two pass the quota (then fail later because no LLM is configured).
    assert statuses[:2] != [429, 429] and statuses[2] == 429
    blocked = client.post("/api/v1/llm-analyses", json=body)
    assert "20" not in blocked.json()["detail"] and "2 次" in blocked.json()["detail"]
    assert int(blocked.headers["retry-after"]) > 0
    # Other actions have their own counters.
    forecast = client.post("/api/v1/forecasts", json={"symbol": "2330.TW", "as_of": "2026-11-03"})
    assert forecast.status_code != 429
