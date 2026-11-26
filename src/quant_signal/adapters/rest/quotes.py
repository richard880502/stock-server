import asyncio
import json
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from quant_signal.adapters.rest.security import Principal, require_principal
from quant_signal.application.quote_cache import (
    PREFIX,
    VIEWER_ADMIT,
    QuoteHub,
    parse_symbols,
    volume_unit,
)
from quant_signal.infrastructure.db.database import session_factory
from quant_signal.infrastructure.db.prices import read_minutes as read_minute_bars


async def read_minutes(symbol, before, limit):
    """Newest ``limit`` minute bars before ``before`` (from ``minute_bars``), oldest first."""
    async with session_factory() as session:
        bars = await read_minute_bars(session, symbol, end=before, limit=limit, newest_first=True)
    return {
        "symbol": symbol,
        "source": "shioaji",
        # Unit of observed_volume_change_lots: "lot" (張) or "contract" (口, futures).
        "volume_unit": volume_unit(symbol),
        "bars": [
            {
                "minute": bar.minute,
                # Minute bars no longer keep trade times; the minute's end bounds them.
                "last_trade_time": bar.minute + timedelta(seconds=59),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "observed_volume_change_lots": bar.volume,
                "is_partial": not bar.official,
            }
            for bar in reversed(bars)
        ],
    }


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
        if not settings.quotes_enabled:
            return await read_minutes(selected[0], before, limit)
        cache_key = PREFIX + f"minutes:{selected[0]}:{before}:{limit}"
        lock_key = cache_key + ":loading"
        token = str(uuid4())
        owned = False
        try:
            cached = await hub.redis.get(cache_key)
            if cached:
                return json.loads(cached)
            owned = bool(await hub.redis.set(lock_key, token, nx=True, ex=15))
            if not owned:
                for _ in range(20):
                    await asyncio.sleep(0.05)
                    cached = await hub.redis.get(cache_key)
                    if cached:
                        return json.loads(cached)
                raise HTTPException(503, "分鐘資料更新中，請稍後再試")
            result = await read_minutes(selected[0], before, limit)
            await hub.redis.set(cache_key, json.dumps(jsonable_encoder(result)), ex=5)
            return result
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "分鐘資料暫時無法取得") from exc
        finally:
            if owned:
                with suppress(Exception):
                    await hub.redis.eval(
                        "if redis.call('GET',KEYS[1])==ARGV[1] then "
                        "return redis.call('DEL',KEYS[1]) end return 0",
                        1,
                        lock_key,
                        token,
                    )

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
