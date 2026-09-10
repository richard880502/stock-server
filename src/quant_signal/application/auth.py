from __future__ import annotations

import hashlib
import secrets
from uuid import UUID

from quant_signal.application.ports import QuantRepository
from quant_signal.domain.models import ApiKey

KEY_PREFIX = "qsk_"


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class ApiKeyService:
    def __init__(self, repository: QuantRepository) -> None:
        self.repository = repository

    async def create(self, name: str) -> tuple[str, ApiKey]:
        raw_key = f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"
        key = await self.repository.create_api_key(
            name=name,
            key_hash=_hash_key(raw_key),
            key_prefix=raw_key[:16],
        )
        return raw_key, key

    async def list(self) -> list[ApiKey]:
        return await self.repository.list_api_keys()

    async def revoke(self, key_id: UUID) -> bool:
        return await self.repository.revoke_api_key(key_id)

    async def verify(self, raw_key: str) -> ApiKey | None:
        key = await self.repository.get_api_key_by_hash(_hash_key(raw_key))
        if key is None or not key.is_active:
            return None
        await self.repository.touch_api_key_last_used(key.id)
        return key
