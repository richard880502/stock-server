from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Cookie, Depends, Header, HTTPException

from quant_signal.application.auth import ApiKeyService
from quant_signal.application.ports import QuantRepository
from quant_signal.application.user_auth import (
    QUOTA_LABELS,
    SESSION_COOKIE,
    hash_token,
    quota_day,
    quota_limit,
    seconds_until_quota_reset,
    utcnow,
)
from quant_signal.domain.models import ApiKey, User
from quant_signal.infrastructure.db import PostgresQuantRepository, session_factory
from quant_signal.settings import get_settings


async def _get_repository() -> AsyncIterator[QuantRepository]:
    async with session_factory() as session:
        yield PostgresQuantRepository(session)


def _extract_raw_key(
    authorization: str | None,
    x_api_key: str | None,
) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[len("bearer "):].strip()
    if x_api_key:
        return x_api_key.strip()
    return None


async def require_api_key(
    repository: Annotated[QuantRepository, Depends(_get_repository)],
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> ApiKey | None:
    settings = get_settings()
    if not settings.api_auth_enabled:
        return None
    raw_key = _extract_raw_key(authorization, x_api_key)
    if not raw_key:
        raise HTTPException(status_code=401, detail="missing API key")
    key = await ApiKeyService(repository).verify(raw_key)
    if key is None:
        raise HTTPException(status_code=401, detail="invalid or revoked API key")
    return key


@dataclass(frozen=True)
class Principal:
    """Who is calling: an API key (trusted tooling), a signed-in user, or nobody."""

    kind: Literal["anonymous", "api_key", "user"]
    user: User | None = None
    api_key: ApiKey | None = None


async def require_principal(
    repository: Annotated[QuantRepository, Depends(_get_repository)],
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> Principal:
    settings = get_settings()
    raw_key = _extract_raw_key(authorization, x_api_key)
    if raw_key:
        key = await ApiKeyService(repository).verify(raw_key)
        if key is not None:
            return Principal("api_key", api_key=key)
        if settings.api_auth_enabled:
            raise HTTPException(status_code=401, detail="invalid or revoked API key")
    if session_token:
        user = await repository.get_user_by_session(hash_token(session_token), now=utcnow())
        if user is not None:
            return Principal("user", user=user)
    if settings.api_auth_enabled:
        raise HTTPException(status_code=401, detail="請先登入")
    return Principal("anonymous")


def enforce_quota(action: str):
    """Per-user daily limit on an expensive endpoint; API keys and anonymous
    callers (auth disabled, e.g. local development) are not limited."""

    async def dependency(
        principal: Annotated[Principal, Depends(require_principal)],
        repository: Annotated[QuantRepository, Depends(_get_repository)],
    ) -> None:
        if principal.kind != "user" or principal.user is None:
            return
        now = utcnow()
        limit = quota_limit(get_settings(), action)
        allowed, _ = await repository.consume_usage(
            principal.user.id, action, quota_day(now), limit=limit
        )
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"今日{QUOTA_LABELS[action]}次數已達上限（{limit} 次），請明天再試。",
                headers={"Retry-After": str(seconds_until_quota_reset(now))},
            )

    return dependency
