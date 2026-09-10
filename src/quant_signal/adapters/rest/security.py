from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from quant_signal.application.auth import ApiKeyService
from quant_signal.application.ports import QuantRepository
from quant_signal.domain.models import ApiKey
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
