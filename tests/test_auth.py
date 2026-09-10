from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from quant_signal.adapters.rest import security
from quant_signal.adapters.rest.main import app
from quant_signal.application.auth import ApiKeyService
from quant_signal.infrastructure.memory import MemoryQuantRepository
from quant_signal.settings import get_settings


async def test_api_key_create_verify_revoke_round_trip() -> None:
    repository = MemoryQuantRepository()
    service = ApiKeyService(repository)

    raw_key, key = await service.create("test-key")

    assert raw_key.startswith("qsk_")
    assert key.key_prefix == raw_key[:16]

    verified = await service.verify(raw_key)
    assert verified is not None
    assert verified.id == key.id

    assert await service.verify("qsk_not-a-real-key") is None

    assert await service.revoke(key.id) is True
    assert await service.verify(raw_key) is None
    assert await service.revoke(key.id) is False


def test_require_api_key_blocks_and_allows_over_rest() -> None:
    repository = MemoryQuantRepository()

    async def override_repository():
        yield repository

    app.dependency_overrides[security._get_repository] = override_repository
    settings = get_settings()
    original_auth_enabled = settings.api_auth_enabled
    settings.api_auth_enabled = True
    try:
        with TestClient(app) as client:
            unauthenticated = client.get("/api/v1/llm/status")
            assert unauthenticated.status_code == 401

            raw_key, _ = asyncio.run(ApiKeyService(repository).create("chatgpt"))
            authenticated = client.get(
                "/api/v1/llm/status",
                headers={"Authorization": f"Bearer {raw_key}"},
            )
            assert authenticated.status_code == 200

            still_open = client.get("/health")
            assert still_open.status_code == 200
    finally:
        settings.api_auth_enabled = original_auth_enabled
        app.dependency_overrides.clear()
