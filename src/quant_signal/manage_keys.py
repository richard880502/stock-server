from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from quant_signal.application.auth import ApiKeyService
from quant_signal.infrastructure.db import PostgresQuantRepository, session_factory


async def _create(name: str) -> None:
    async with session_factory() as session:
        raw_key, key = await ApiKeyService(PostgresQuantRepository(session)).create(name)
    print(
        json.dumps(
            {
                "id": str(key.id),
                "name": key.name,
                "key": raw_key,
                "note": "store this key now, it cannot be retrieved again",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


async def _list() -> None:
    async with session_factory() as session:
        keys = await ApiKeyService(PostgresQuantRepository(session)).list()
    print(
        json.dumps(
            [
                {
                    "id": str(key.id),
                    "name": key.name,
                    "key_prefix": key.key_prefix,
                    "created_at": key.created_at.isoformat(),
                    "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
                    "last_used_at": key.last_used_at.isoformat()
                    if key.last_used_at
                    else None,
                }
                for key in keys
            ],
            ensure_ascii=False,
            indent=2,
        )
    )


async def _revoke(key_id: UUID) -> None:
    async with session_factory() as session:
        revoked = await ApiKeyService(PostgresQuantRepository(session)).revoke(key_id)
    print(json.dumps({"id": str(key_id), "revoked": revoked}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage API keys for REST and MCP access.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Issue a new API key.")
    create_parser.add_argument("--name", required=True)

    subparsers.add_parser("list", help="List all API keys.")

    revoke_parser = subparsers.add_parser("revoke", help="Revoke an API key.")
    revoke_parser.add_argument("--id", required=True, dest="key_id")

    args = parser.parse_args()
    if args.command == "create":
        asyncio.run(_create(args.name))
    elif args.command == "list":
        asyncio.run(_list())
    elif args.command == "revoke":
        asyncio.run(_revoke(UUID(args.key_id)))


if __name__ == "__main__":
    main()
