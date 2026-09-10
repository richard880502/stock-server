from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated
from uuid import UUID

import httpx
import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from quant_signal.adapters.rest.security import require_api_key
from quant_signal.application.llm_analysis import (
    DebateAnalysisService,
    LLMAnalysisService,
    create_llm_client,
)
from quant_signal.application.ports import QuantRepository
from quant_signal.application.services import (
    BacktestJobService,
    ChipFlowService,
    DataStatusService,
    MarketEnvironmentService,
    SignalService,
)
from quant_signal.domain.models import (
    BacktestJob,
    BacktestSpec,
    ChipFlowSnapshot,
    DataStatus,
    DebateAnalysisReport,
    LLMAnalysisReport,
    MarketEnvironmentSnapshot,
    SignalSnapshot,
)
from quant_signal.infrastructure.db import PostgresQuantRepository, session_factory
from quant_signal.settings import get_settings

SYMBOL_PATTERN = re.compile(r"^[A-Z0-9.^=_-]{1,32}$")


class LLMAnalysisRequest(BaseModel):
    symbol: str
    as_of: date
    market: str = "TW"
    benchmark: str | None = "^TWII"
    strategy_version: str = "regime_v1"
    backtest_job_id: UUID | None = None


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
    )
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "quant-signal-server"}

    @router.get("/data/status", response_model=DataStatus)
    async def data_status(
        repository: Annotated[QuantRepository, Depends(get_repository)],
    ) -> DataStatus:
        return await DataStatusService(repository).get()

    @router.get("/signals/{symbol}", response_model=SignalSnapshot)
    async def analyze_symbol(
        symbol: str,
        repository: Annotated[QuantRepository, Depends(get_repository)],
        as_of: Annotated[date | None, Query()] = None,
        benchmark: str | None = None,
        strategy_version: str = "regime_v1",
    ) -> SignalSnapshot:
        service = SignalService(repository)
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
                service = LLMAnalysisService(repository, client)
                yield encode_event("meta", {"model": client.model_name})
                async for token in service.stream_guidance(
                    symbol=normalize_symbol(request.symbol),
                    as_of=request.as_of,
                    market=normalize_symbol(request.market),
                    benchmark=normalize_symbol(request.benchmark)
                    if request.benchmark
                    else None,
                    strategy_version=request.strategy_version,
                    backtest_job_id=request.backtest_job_id,
                ):
                    yield encode_event("delta", {"text": token})
                yield encode_event("done", {})
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
