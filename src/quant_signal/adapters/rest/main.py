from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import secrets
from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

import httpx
import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from quant_signal.adapters.rest.security import Principal, enforce_quota, require_principal
from quant_signal.application.data_sync import (
    DataSyncPending,
    TpexDataSyncService,
    TwseDataSyncService,
)
from quant_signal.application.llm_analysis import (
    DebateAnalysisService,
    LLMAnalysisService,
    create_judge_llm_client,
    create_llm_client,
)
from quant_signal.application.market_rankings import MarketRankingService
from quant_signal.application.ports import QuantRepository
from quant_signal.application.services import (
    BacktestJobService,
    ChipFlowService,
    DataStatusService,
    InstrumentSearchService,
    MarketEnvironmentService,
    NewsSentimentService,
    SignalService,
)
from quant_signal.application.user_auth import (
    SESSION_COOKIE,
    STATE_COOKIE,
    GoogleLoginError,
    cookie_secure,
    email_allowed,
    exchange_code,
    google_enabled,
    hash_token,
    new_session_token,
    parse_id_token,
    session_expiry,
    start_login,
    unsign,
    utcnow,
)
from quant_signal.domain.models import (
    BacktestJob,
    BacktestSpec,
    ChipFlowSnapshot,
    DataStatus,
    DebateAnalysisReport,
    Instrument,
    LLMAnalysisReport,
    MarketDataStatus,
    MarketEnvironmentSnapshot,
    MarketFlowSeries,
    MarketRanking,
    NewsSentimentSnapshot,
    RankingMetric,
    SignalSnapshot,
)
from quant_signal.forecast import ForecastReport, ForecastRequest, forecast
from quant_signal.infrastructure.db import PostgresQuantRepository, session_factory
from quant_signal.infrastructure.providers import TpexProvider, TwseProvider
from quant_signal.settings import get_settings

SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.^=_-]{1,32}$")


class LLMAnalysisRequest(BaseModel):
    symbol: str
    as_of: date
    market: str = "TW"
    benchmark: str | None = "^TWII"
    strategy_version: str = "regime_v1"
    backtest_job_id: UUID | None = None


class LoginRequest(BaseModel):
    password: str


class SymbolSyncRequest(BaseModel):
    symbol: str
    start: date
    end: date | None = None


async def get_repository() -> AsyncIterator[QuantRepository]:
    async with session_factory() as session:
        yield PostgresQuantRepository(session)


def normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="invalid symbol")
    return normalized


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="Quant Signal Server",
        version="0.1.0",
        description="Point-in-time quantitative signals and durable backtest jobs.",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-API-Key"],
        # Lets the dashboard read the sync-pending retry delay cross-origin.
        expose_headers=["Retry-After"],
    )
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_principal)])

    @application.exception_handler(DataSyncPending)
    async def data_sync_pending(_: Request, exc: DataSyncPending) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc), "sync_pending": True, "symbol": exc.symbol},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "quant-signal-server"}


    def _login_redirect(path: str, *, error: str | None = None) -> RedirectResponse:
        base = get_settings().frontend_url.rstrip("/")
        target = f"{base}/?login_error={error}" if error else f"{base}{path}"
        response = RedirectResponse(target, status_code=303)
        response.delete_cookie(STATE_COOKIE, path="/api/v1/auth/google")
        return response

    @application.get("/api/v1/auth/config")
    async def auth_config() -> dict[str, bool]:
        settings = get_settings()
        return {
            "google": google_enabled(settings),
            "password": bool(settings.dashboard_password.get_secret_value()),
        }

    @application.get("/api/v1/auth/google/login")
    async def google_login(next: str | None = None) -> RedirectResponse:
        settings = get_settings()
        if not google_enabled(settings):
            raise HTTPException(status_code=503, detail="Google 登入尚未設定")
        url, state_cookie = start_login(settings, next_path=next, now=utcnow())
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(
            STATE_COOKIE,
            state_cookie,
            max_age=600,
            httponly=True,
            samesite="lax",
            secure=cookie_secure(settings),
            path="/api/v1/auth/google",
        )
        return response

    @application.get("/api/v1/auth/google/callback")
    async def google_callback(
        request: Request,
        repository: Annotated[QuantRepository, Depends(get_repository)],
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> RedirectResponse:
        settings = get_settings()
        now = utcnow()
        if error:
            code_name = "access_denied" if error == "access_denied" else "failed"
            return _login_redirect("/", error=code_name)
        pending = unsign(request.cookies.get(STATE_COOKIE), settings, now=now)
        if (
            not pending
            or not code
            or not state
            or not hmac.compare_digest(state, str(pending.get("state", "")))
        ):
            return _login_redirect("/", error="state")
        try:
            id_token = await exchange_code(settings, code, str(pending["verifier"]))
            identity = parse_id_token(
                id_token,
                client_id=settings.google_client_id,
                nonce=str(pending["nonce"]),
                now=now,
            )
        except GoogleLoginError as exc:
            logging.getLogger(__name__).warning("google login failed: %s (%s)", exc.code, exc)
            return _login_redirect("/", error=exc.code)
        if not email_allowed(identity.email, settings):
            return _login_redirect("/", error="email_not_allowed")
        user = await repository.upsert_google_user(
            google_sub=identity.sub,
            email=identity.email,
            name=identity.name,
            picture=identity.picture,
        )
        if not user.is_active:
            return _login_redirect("/", error="disabled")
        raw, hashed = new_session_token()
        await repository.create_user_session(
            user_id=user.id, token_hash=hashed, expires_at=session_expiry(settings, now=now)
        )
        response = _login_redirect(str(pending.get("next") or "/"))
        response.set_cookie(
            SESSION_COOKIE,
            raw,
            max_age=settings.session_ttl_days * 86400,
            httponly=True,
            samesite="lax",
            secure=cookie_secure(settings),
            path="/",
        )
        return response

    @application.get("/api/v1/auth/me")
    async def auth_me(
        principal: Annotated[Principal, Depends(require_principal)],
    ) -> dict:
        if principal.kind != "user" or principal.user is None:
            raise HTTPException(status_code=401, detail="未登入")
        user = principal.user
        return {"id": str(user.id), "email": user.email, "name": user.name, "picture": user.picture}

    @application.post("/api/v1/auth/logout")
    async def auth_logout(
        request: Request,
        repository: Annotated[QuantRepository, Depends(get_repository)],
    ) -> JSONResponse:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            await repository.delete_user_session(hash_token(token))
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @application.post("/api/v1/auth/login")
    async def login(request: LoginRequest) -> dict[str, str]:
        dashboard_password = get_settings().dashboard_password.get_secret_value()
        if not dashboard_password or not secrets.compare_digest(
            request.password, dashboard_password
        ):
            raise HTTPException(status_code=401, detail="incorrect password")
        return {"api_key": get_settings().dashboard_api_key.get_secret_value()}

    @router.get("/forecasts/status")
    async def forecast_status() -> dict:
        url = get_settings().kronos_url
        ready = False
        if url:
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    result = await client.get(f"{url.rstrip('/')}/health")
                    ready = result.is_success
            except httpx.HTTPError:
                pass
        return {"bootstrap": True, "kronos": ready, "kronos_configured": bool(url)}

    @router.post(
        "/forecasts",
        response_model=ForecastReport,
        dependencies=[Depends(enforce_quota("forecast"))],
    )
    async def create_forecast(
        request: ForecastRequest,
        repository: Annotated[QuantRepository, Depends(get_repository)],
    ) -> ForecastReport:
        normalized = request.model_copy(update={"symbol": normalize_symbol(request.symbol)})
        bars = await repository.list_bars(normalized.symbol, end=normalized.as_of)
        try:
            settings = get_settings()
            return await forecast(
                bars,
                normalized,
                kronos_url=settings.kronos_url,
                kronos_api_key=settings.kronos_api_key.get_secret_value(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503, detail="Kronos 服務暫時無法使用，請稍後重試"
            ) from exc

    @router.get("/data/status", response_model=DataStatus)
    async def data_status(
        repository: Annotated[QuantRepository, Depends(get_repository)],
    ) -> DataStatus:
        return await DataStatusService(repository).get()

    @router.get("/instruments/search", response_model=list[Instrument])
    async def search_instruments(
        repository: Annotated[QuantRepository, Depends(get_repository)],
        q: Annotated[str, Query(min_length=1)],
    ) -> list[Instrument]:
        return await InstrumentSearchService(repository).search(q)

    @router.post("/sync/symbol", dependencies=[Depends(enforce_quota("sync"))])
    async def sync_symbol(
        request: SymbolSyncRequest,
        repository: Annotated[QuantRepository, Depends(get_repository)],
    ) -> dict:
        normalized = normalize_symbol(request.symbol)
        is_tpex = normalized.endswith(".TWO") or normalized == "^TWOII"
        try:
            async with (
                asyncio.timeout(90),
                TwseProvider() as twse_provider,
                TpexProvider() as tpex_provider,
            ):
                service = (
                    TpexDataSyncService(repository, tpex_provider)
                    if is_tpex
                    else TwseDataSyncService(repository, twse_provider)
                )
                report = await service.sync_symbol(
                    normalized,
                    start=request.start,
                    end=request.end or date.today(),
                )
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="官方資料同步逾時，請稍後再試") from exc
        except (ValueError, RuntimeError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=502, detail="官方資料同步失敗，請稍後再試") from exc
        return report.model_dump(mode="json")

    @router.get("/signals/{symbol}", response_model=SignalSnapshot)
    async def analyze_symbol(
        symbol: str,
        repository: Annotated[QuantRepository, Depends(get_repository)],
        as_of: Annotated[date | None, Query()] = None,
        benchmark: str | None = None,
        strategy_version: str = "regime_v1",
    ) -> SignalSnapshot:
        service = SignalService(repository, auto_sync=True)
        try:
            return await service.analyze(
                normalize_symbol(symbol),
                as_of=as_of or date.today(),
                benchmark=normalize_symbol(benchmark) if benchmark else None,
                strategy_version=strategy_version,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get(
        "/chip-flows/{symbol}",
        response_model=ChipFlowSnapshot,
    )
    async def analyze_chip_flow(
        symbol: str,
        repository: Annotated[QuantRepository, Depends(get_repository)],
        as_of: Annotated[date | None, Query()] = None,
        strategy_version: str = "chip_flow_v1",
    ) -> ChipFlowSnapshot:
        try:
            return await ChipFlowService(repository).analyze(
                normalize_symbol(symbol),
                as_of=as_of or date.today(),
                strategy_version=strategy_version,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get(
        "/news-sentiment/{symbol}",
        response_model=NewsSentimentSnapshot,
    )
    async def analyze_news_sentiment(
        symbol: str,
        repository: Annotated[QuantRepository, Depends(get_repository)],
        as_of: Annotated[date | None, Query()] = None,
        strategy_version: str = "news_sentiment_v1",
    ) -> NewsSentimentSnapshot:
        try:
            return await NewsSentimentService(repository).analyze(
                normalize_symbol(symbol),
                as_of=as_of or date.today(),
                strategy_version=strategy_version,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post(
        "/backtests",
        response_model=BacktestJob,
        status_code=202,
        dependencies=[Depends(enforce_quota("backtest"))],
    )
    async def start_backtest(
        spec: BacktestSpec,
        repository: Annotated[QuantRepository, Depends(get_repository)],
    ) -> BacktestJob:
        normalized = spec.model_copy(
            update={
                "symbol": normalize_symbol(spec.symbol),
                "benchmark": normalize_symbol(spec.benchmark) if spec.benchmark else None,
                "market": normalize_symbol(spec.market) if spec.market else None,
            }
        )
        return await BacktestJobService(repository).start(normalized)

    @router.get(
        "/market-environments/{market}",
        response_model=MarketEnvironmentSnapshot,
    )
    async def get_market_environment(
        market: str,
        repository: Annotated[QuantRepository, Depends(get_repository)],
        as_of: Annotated[date | None, Query()] = None,
        strategy_version: str = "market_env_v1",
    ) -> MarketEnvironmentSnapshot:
        try:
            return await MarketEnvironmentService(repository).analyze(
                normalize_symbol(market),
                as_of=as_of or date.today(),
                strategy_version=strategy_version,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/market/rankings", response_model=MarketRanking)
    async def market_rankings(
        repository: Annotated[QuantRepository, Depends(get_repository)],
        metric: RankingMetric = "foreign_net",
        market: Literal["ALL", "TW", "TWO"] = "ALL",
        window: Annotated[int, Query(ge=1, le=60)] = 1,
        order: Literal["desc", "asc"] = "desc",
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        as_of: Annotated[date | None, Query()] = None,
    ) -> MarketRanking:
        return await MarketRankingService(
            repository, backfill_days=get_settings().sync_market_lookback_days
        ).ranking(
            metric, market=market, window=window, order=order, limit=limit, as_of=as_of
        )

    @router.get("/market/flows", response_model=MarketFlowSeries)
    async def market_flows(
        repository: Annotated[QuantRepository, Depends(get_repository)],
        market: Literal["ALL", "TW", "TWO"] = "ALL",
        days: Annotated[int, Query(ge=5, le=250)] = 60,
        as_of: Annotated[date | None, Query()] = None,
    ) -> MarketFlowSeries:
        return await MarketRankingService(
            repository, backfill_days=get_settings().sync_market_lookback_days
        ).flows(market=market, days=days, as_of=as_of)

    @router.get("/market/data-status", response_model=list[MarketDataStatus])
    async def market_data_status(
        repository: Annotated[QuantRepository, Depends(get_repository)],
    ) -> list[MarketDataStatus]:
        return await MarketRankingService(repository).status()

    @router.get("/backtests/{job_id}", response_model=BacktestJob)
    async def get_backtest(
        job_id: UUID,
        repository: Annotated[QuantRepository, Depends(get_repository)],
    ) -> BacktestJob:
        job = await BacktestJobService(repository).get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="backtest job not found")
        return job

    @router.get("/llm/status")
    async def llm_status() -> dict:
        settings = get_settings()
        return {
            "enabled": settings.llm_enabled,
            "configured": not settings.llm_errors(),
            "missing": settings.llm_errors(),
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
        }

    @router.post(
        "/llm-analyses",
        response_model=LLMAnalysisReport,
        dependencies=[Depends(enforce_quota("llm"))],
    )
    async def create_llm_analysis(
        request: LLMAnalysisRequest,
        repository: Annotated[QuantRepository, Depends(get_repository)],
    ) -> LLMAnalysisReport:
        settings = get_settings()
        if settings.llm_errors():
            raise HTTPException(
                status_code=503,
                detail=f"LLM is not configured: {', '.join(settings.llm_errors())}",
            )
        try:
            service = LLMAnalysisService(
                repository,
                create_llm_client(settings),
                auto_sync=True,
            )
            return await service.analyze(
                symbol=normalize_symbol(request.symbol),
                as_of=request.as_of,
                market=normalize_symbol(request.market),
                benchmark=normalize_symbol(request.benchmark) if request.benchmark else None,
                strategy_version=request.strategy_version,
                backtest_job_id=request.backtest_job_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post(
        "/debate-analyses",
        response_model=DebateAnalysisReport,
        dependencies=[Depends(enforce_quota("llm"))],
    )
    async def create_debate_analysis(
        request: LLMAnalysisRequest,
        repository: Annotated[QuantRepository, Depends(get_repository)],
    ) -> DebateAnalysisReport:
        settings = get_settings()
        if settings.llm_errors():
            raise HTTPException(
                status_code=503,
                detail=f"LLM is not configured: {', '.join(settings.llm_errors())}",
            )
        try:
            service = DebateAnalysisService(
                repository,
                create_llm_client(settings),
                judge_client=create_judge_llm_client(settings),
                auto_sync=True,
            )
            return await service.analyze(
                symbol=normalize_symbol(request.symbol),
                as_of=request.as_of,
                market=normalize_symbol(request.market),
                benchmark=normalize_symbol(request.benchmark) if request.benchmark else None,
                strategy_version=request.strategy_version,
                backtest_job_id=request.backtest_job_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.post("/llm-analyses/stream", dependencies=[Depends(enforce_quota("llm"))])
    async def stream_llm_guidance(
        request: LLMAnalysisRequest,
        repository: Annotated[QuantRepository, Depends(get_repository)],
    ) -> StreamingResponse:
        settings = get_settings()
        if settings.llm_errors():
            raise HTTPException(
                status_code=503,
                detail=f"LLM is not configured: {', '.join(settings.llm_errors())}",
            )

        def encode_event(event: str, payload: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        async def event_stream() -> AsyncIterator[str]:
            try:
                client = create_llm_client(settings)
                service = LLMAnalysisService(repository, client, auto_sync=True)
                yield encode_event("meta", {"model": client.model_name})
                async for token in service.stream_guidance(
                    symbol=normalize_symbol(request.symbol),
                    as_of=request.as_of,
                    market=normalize_symbol(request.market),
                    benchmark=normalize_symbol(request.benchmark) if request.benchmark else None,
                    strategy_version=request.strategy_version,
                    backtest_job_id=request.backtest_job_id,
                ):
                    yield encode_event("delta", {"text": token})
                yield encode_event("done", {})
            except DataSyncPending as exc:
                yield encode_event("error", {"message": str(exc), "status": 503})
            except LookupError as exc:
                yield encode_event("error", {"message": str(exc), "status": 404})
            except (ValueError, httpx.HTTPError) as exc:
                yield encode_event("error", {"message": str(exc), "status": 502})
            except Exception:
                yield encode_event(
                    "error",
                    {"message": "LLM 串流連線中斷，請稍後再試", "status": 502},
                )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    application.include_router(router)
    return application


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "quant_signal.adapters.rest.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
