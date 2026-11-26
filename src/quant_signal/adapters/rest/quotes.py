import asyncio
import json
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from quant_signal.adapters.rest.security import Principal, require_principal
from quant_signal.application.quote_cache import PREFIX, VIEWER_ADMIT, QuoteHub, parse_symbols
from quant_signal.infrastructure.db.database import session_factory
from quant_signal.infrastructure.db.models import QuoteMinuteRow


def install_quotes(app, settings):
    hub = QuoteHub(settings)
    old_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with old_lifespan(application):
            try:
                yield
            finally:
                await hub.close()

    app.router.lifespan_context = lifespan
    router = APIRouter(prefix="/api/v1/quotes", dependencies=[Depends(require_principal)])

    @router.get("/minutes")
    async def minutes(
        symbol: str,
        before: datetime | None = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 120,
    ):
        try:
            selected = parse_symbols(symbol)
            if len(selected) != 1 or (before and before.tzinfo is None):
                raise ValueError("請提供單一股票代號與包含時區的時間")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        statement = select(QuoteMinuteRow).where(QuoteMinuteRow.symbol == selected[0])
        if before:
            statement = statement.where(QuoteMinuteRow.minute < before)
        async with session_factory() as session:
            rows = (
                await session.scalars(statement.order_by(QuoteMinuteRow.minute.desc()).limit(limit))
            ).all()
        return {
            "symbol": selected[0],
            "source": "shioaji",
            "bars": [
                {
                    "minute": r.minute,
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "observed_volume_change_lots": max(
                        0, r.last_total_volume - r.first_total_volume
                    ),
                    "is_partial": r.is_partial,
                }
                for r in reversed(rows)
            ],
        }

    @router.get("/stream")
    async def stream(
        principal: Annotated[Principal, Depends(require_principal)],
        symbols: str = Query(max_length=400),
    ):
        try:
            selected = parse_symbols(symbols)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if settings.quotes_enabled:
            identity = (
                str(principal.user.id)
                if principal.user
                else str(principal.api_key.id)
                if principal.api_key
                else "local"
            )
            viewer_key = PREFIX + "viewers:" + identity
            token = str(uuid4())
            try:
                admitted = await hub.redis.eval(
                    VIEWER_ADMIT,
                    1,
                    viewer_key,
                    time.time(),
                    settings.quotes_max_connections_per_user,
                    token,
                )
                if not admitted:
                    raise ValueError("此帳號行情連線過多，請關閉其他分頁")
                await hub.join(selected)
            except ValueError as exc:
                await hub.redis.zrem(viewer_key, token)
                raise HTTPException(429, str(exc), headers={"Retry-After": "60"}) from exc
            except Exception as exc:
                with suppress(Exception):
                    await hub.redis.zrem(viewer_key, token)
                raise HTTPException(503, "行情快取暫時無法連線") from exc

        async def events():
            try:
                # Rotate periodically so authentication is rechecked on reconnect.
                for _ in range(300):
                    payload = {
                        "status": hub.status if settings.quotes_enabled else "disabled",
                        "quotes": [hub.quotes[s] for s in selected if s in hub.quotes],
                    }
                    yield "event: state\ndata: " + json.dumps(payload) + "\n\n"
                    if not settings.quotes_enabled:
                        break
                    await asyncio.sleep(1)
            finally:
                if settings.quotes_enabled:
                    hub.leave(selected)
                    with suppress(Exception):
                        await hub.redis.zrem(viewer_key, token)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    app.include_router(router)
