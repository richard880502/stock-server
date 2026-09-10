from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, Header, HTTPException, Query

SEARXNG_PROXY_KEY = os.environ["SEARXNG_PROXY_KEY"]
SEARXNG_UPSTREAM = os.environ.get("SEARXNG_UPSTREAM", "http://searxng.zeabur.internal:8080")

app = FastAPI(title="searxng-proxy")


def _require_key(authorization: str | None) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing API key")
    if authorization[len("bearer "):].strip() != SEARXNG_PROXY_KEY:
        raise HTTPException(status_code=401, detail="invalid API key")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "searxng-proxy"}


@app.get("/search")
async def search(
    q: str,
    authorization: str | None = Header(default=None),
    category: str = Query(default="general"),
) -> dict:
    _require_key(authorization)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{SEARXNG_UPSTREAM}/search",
            params={"q": q, "format": "json", "categories": category},
        )
        response.raise_for_status()
        return response.json()
