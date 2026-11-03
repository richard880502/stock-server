"""Optional shared-secret check for the prediction endpoint.

Unset ``KRONOS_API_KEY`` keeps the old behaviour (open, for a private network).
Set it before exposing the service anywhere reachable. ``/health`` stays open so
orchestrators can probe it.
"""

import hmac
import os

from fastapi import Header, HTTPException


def auth_required() -> bool:
    return bool(os.environ.get("KRONOS_API_KEY", "").strip())


async def require_api_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    expected = os.environ.get("KRONOS_API_KEY", "").strip()
    if not expected:
        return
    supplied = x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[len("bearer "):].strip()
    if not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(
            status_code=401,
            detail="invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
