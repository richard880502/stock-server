"""Internal health probes for the independent quote collector."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse


@dataclass
class QuoteHealth:
    connected: bool = False
    leader: bool = False
    alive: float = field(default_factory=time.monotonic)


def create_health_app(health: QuoteHealth, redis: Any) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def is_live() -> bool:
        return time.monotonic() - health.alive < 30

    @app.get("/livez")
    async def live() -> JSONResponse:
        live_now = is_live()
        return JSONResponse({"live": live_now}, status_code=200 if live_now else 503)

    @app.get("/readyz")
    @app.get("/health")
    async def ready() -> JSONResponse:
        ready_now = False
        if is_live() and health.leader and health.connected:
            try:
                ready_now = bool(await asyncio.wait_for(redis.ping(), timeout=1.0))
            except Exception:
                ready_now = False
        return JSONResponse({"ready": ready_now}, status_code=200 if ready_now else 503)

    return app


async def serve(health: QuoteHealth, redis: Any, host: str = "0.0.0.0", port: int = 8003) -> None:
    server = uvicorn.Server(
        uvicorn.Config(
            create_health_app(health, redis),
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    await server.serve()
