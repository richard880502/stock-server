"""Google sign-in (authorization-code flow with PKCE), signed state and sessions.

The API owns the whole flow: the browser is redirected to Google, Google returns
to the API with a one-time ``code``, and the API exchanges it for an ID token over
TLS using the client secret. Because the token comes straight from Google's token
endpoint, its signature does not need to be re-verified (OpenID Connect Core
3.1.3.7); issuer, audience, expiry, nonce and ``email_verified`` are checked.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from quant_signal.settings import Settings

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
SESSION_COOKIE = "qs_session"
STATE_COOKIE = "qs_oauth"
STATE_TTL_SECONDS = 600
TAIPEI = ZoneInfo("Asia/Taipei")

_EPHEMERAL_SECRET = secrets.token_bytes(32)


class GoogleLoginError(Exception):
    """A login attempt failed; ``code`` is safe to show to the user."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(detail or code)


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str | None
    picture: str | None


def google_enabled(settings: Settings) -> bool:
    return bool(
        settings.google_client_id
        and settings.google_client_secret.get_secret_value()
        and settings.google_redirect_uri
    )


def _secret(settings: Settings) -> bytes:
    configured = settings.session_secret.get_secret_value()
    if configured:
        return configured.encode()
    logger.warning("SESSION_SECRET is not set; login state will not survive a restart")
    return _EPHEMERAL_SECRET


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(payload: dict[str, Any], settings: Settings) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    mac = hmac.new(_secret(settings), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def unsign(token: str | None, settings: Settings, *, now: datetime) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, _, signature = token.partition(".")
    expected = hmac.new(_secret(settings), body.encode(), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(_unb64(signature), expected):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or float(payload.get("exp", 0)) <= now.timestamp():
        return None
    return payload


def new_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def safe_next(path: str | None) -> str:
    """Only same-site relative paths may be used as the post-login destination."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path


def start_login(settings: Settings, *, next_path: str | None, now: datetime) -> tuple[str, str]:
    """Return (authorize_url, signed state cookie value)."""
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = new_pkce()
    cookie = sign(
        {
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
            "next": safe_next(next_path),
            "exp": (now + timedelta(seconds=STATE_TTL_SECONDS)).timestamp(),
        },
        settings,
    )
    query = urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.google_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )
    return f"{GOOGLE_AUTH_URL}?{query}", cookie


async def exchange_code(
    settings: Settings,
    code: str,
    verifier: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    owns = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret.get_secret_value(),
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
    except httpx.HTTPError as exc:
        raise GoogleLoginError("google_unreachable", str(exc)) from exc
    finally:
        if owns:
            await client.aclose()
    if response.status_code != 200:
        raise GoogleLoginError("token_exchange_failed", f"HTTP {response.status_code}")
    token = response.json().get("id_token")
    if not isinstance(token, str):
        raise GoogleLoginError("token_exchange_failed", "no id_token in response")
    return token


def parse_id_token(
    id_token: str, *, client_id: str, nonce: str, now: datetime
) -> GoogleIdentity:
    try:
        claims = json.loads(_unb64(id_token.split(".")[1]))
    except (IndexError, ValueError, json.JSONDecodeError) as exc:
        raise GoogleLoginError("invalid_token", "malformed id_token") from exc
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise GoogleLoginError("invalid_token", "bad issuer")
    if client_id not in audiences:
        raise GoogleLoginError("invalid_token", "bad audience")
    if float(claims.get("exp", 0)) <= now.timestamp():
        raise GoogleLoginError("invalid_token", "expired")
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise GoogleLoginError("invalid_token", "nonce mismatch")
    if claims.get("email_verified") is not True:
        raise GoogleLoginError("email_unverified")
    sub, email = claims.get("sub"), claims.get("email")
    if not isinstance(sub, str) or not isinstance(email, str) or not sub or not email:
        raise GoogleLoginError("invalid_token", "missing sub or email")
    return GoogleIdentity(
        sub=sub,
        email=email.lower(),
        name=claims.get("name") if isinstance(claims.get("name"), str) else None,
        picture=claims.get("picture") if isinstance(claims.get("picture"), str) else None,
    )


def email_allowed(email: str, settings: Settings) -> bool:
    """Both allow-lists empty means any verified Google account may sign in."""
    emails = {e.strip().lower() for e in settings.auth_allowed_emails.split(",") if e.strip()}
    domains = {
        d.strip().lower().lstrip("@")
        for d in settings.auth_allowed_email_domains.split(",")
        if d.strip()
    }
    if not emails and not domains:
        return True
    address = email.lower()
    return address in emails or address.rsplit("@", 1)[-1] in domains


def new_session_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def session_expiry(settings: Settings, *, now: datetime) -> datetime:
    return now + timedelta(days=settings.session_ttl_days)


def cookie_secure(settings: Settings) -> bool:
    return settings.google_redirect_uri.startswith("https://")


QUOTA_LABELS = {
    "llm": "LLM 分析",
    "forecast": "價格預測",
    "sync": "資料同步",
    "backtest": "策略回測",
}


def quota_limit(settings: Settings, action: str) -> int:
    return {
        "llm": settings.quota_llm_per_day,
        "forecast": settings.quota_forecast_per_day,
        "sync": settings.quota_sync_per_day,
        "backtest": settings.quota_backtest_per_day,
    }[action]


def quota_day(now: datetime) -> Any:
    return now.astimezone(TAIPEI).date()


def seconds_until_quota_reset(now: datetime) -> int:
    local = now.astimezone(TAIPEI)
    tomorrow = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(int((tomorrow - local).total_seconds()), 1)


def utcnow() -> datetime:
    return datetime.now(UTC)
