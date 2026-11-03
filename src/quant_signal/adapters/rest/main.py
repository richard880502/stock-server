from __future__ import annotations

import asyncio
import json
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
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from quant_signal.adapters.rest.security import require_api_key
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
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-API-Key"],
        # Lets the dashboard read the sync-pending retry delay cross-origin.
        expose_headers=["Retry-After"],
    )
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])

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

    @router.post("/forecasts", response_model=ForecastReport)
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

    @router.post("/sync/symbol")
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

    @router.post("/llm-analyses/stream")
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
