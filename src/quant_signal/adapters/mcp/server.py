from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from quant_signal.application.auth import ApiKeyService
from quant_signal.application.llm_analysis import (
    AnalysisContextService,
    DebateAnalysisService,
    LLMAnalysisService,
    create_llm_client,
)
from quant_signal.application.services import (
    BacktestJobService,
    ChipFlowService,
    DataStatusService,
    InstrumentSearchService,
    MarketEnvironmentService,
    SignalService,
)
from quant_signal.domain.models import BacktestSpec, JobStatus
from quant_signal.infrastructure.db import PostgresQuantRepository, session_factory
from quant_signal.settings import get_settings

settings = get_settings()
mcp = FastMCP(
    "Quant Signal Server",
    instructions=(
        "Use these tools for deterministic market signals and point-in-time backtests. "
        "Always inspect data availability and sample size before presenting conclusions. "
        "If you only have a company name or an uncertain ticker, call search_symbol "
        "first to resolve it to an exact symbol before calling any other tool."
    ),
    host=settings.mcp_host,
    port=settings.mcp_port,
    stateless_http=settings.mcp_stateless_http,
    json_response=True,
)


@mcp.resource("quant://server/info")
def server_info() -> str:
    return (
        '{"name":"quant-signal-server","version":"0.1.0",'
        '"capabilities":["data_status","symbol_search","signals","market_environment",'
        '"chip_flows","broker_branches","alerts","backtests","llm"]}'
    )


@mcp.tool()
async def get_data_status() -> dict[str, Any]:
    """List real and synthetic series, date coverage, source, and observation counts."""
    async with session_factory() as session:
        status = await DataStatusService(PostgresQuantRepository(session)).get()
        return status.model_dump(mode="json")


@mcp.tool()
async def search_symbol(query: str) -> list[dict[str, Any]]:
    """Resolve a company name or partial ticker (e.g. "台積電" or "2330") to exact symbols."""
    async with session_factory() as session:
        matches = await InstrumentSearchService(PostgresQuantRepository(session)).search(
            query
        )
        return [instrument.model_dump(mode="json") for instrument in matches]


async def _analyze_symbol(
    symbol: str,
    as_of: str,
    benchmark: str | None = None,
    strategy_version: str = "regime_v1",
) -> dict[str, Any]:
    """Analyze a market index, ETF, or stock using deterministic daily-bar factors."""
    analysis_date = date.fromisoformat(as_of)
    async with session_factory() as session:
        repository = PostgresQuantRepository(session)
        snapshot = await SignalService(repository).analyze(
            symbol.strip().upper(),
            as_of=analysis_date,
            benchmark=benchmark.strip().upper() if benchmark else None,
            strategy_version=strategy_version,
        )
        return snapshot.model_dump(mode="json")


@mcp.tool()
async def analyze_symbol(
    symbol: str,
    as_of: str,
    benchmark: str | None = None,
    strategy_version: str = "regime_v1",
) -> dict[str, Any]:
    """Analyze a market index, ETF, or stock using deterministic daily-bar factors."""
    return await _analyze_symbol(symbol, as_of, benchmark, strategy_version)


@mcp.tool()
async def get_market_regime(
    market_symbol: str,
    as_of: str,
    strategy_version: str = "market_env_v1",
) -> dict[str, Any]:
    """Return breadth/capital regime; ^TWII is accepted as a TW compatibility alias."""
    market = "TW" if market_symbol.strip().upper() == "^TWII" else market_symbol
    return _market_summary(
        await _get_market_environment(market, as_of, strategy_version)
    )


async def _get_market_environment(
    market: str,
    as_of: str,
    strategy_version: str = "market_env_v1",
) -> dict[str, Any]:
    async with session_factory() as session:
        snapshot = await MarketEnvironmentService(
            PostgresQuantRepository(session)
        ).analyze(
            market.strip().upper(),
            as_of=date.fromisoformat(as_of),
            strategy_version=strategy_version,
        )
        return snapshot.model_dump(mode="json")


@mcp.tool()
async def get_market_environment(
    market: str,
    as_of: str,
    strategy_version: str = "market_env_v1",
) -> dict[str, Any]:
    """Return the complete point-in-time market breadth and capital environment."""
    return await _get_market_environment(market, as_of, strategy_version)


def _market_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "market": result["market"],
        "as_of": result["as_of"],
        "regime": result["regime"],
        "score": result["score"],
        "confidence": result["confidence"],
        "factors": result["factors"],
        "alerts": result["alerts"],
        "data_available_at": result["data_available_at"],
    }


@mcp.tool()
async def get_signal_alerts(
    symbol: str,
    as_of: str,
    benchmark: str | None = None,
) -> dict[str, Any]:
    """Return anomaly alerts and their numeric evidence for one symbol."""
    result = await _analyze_symbol(symbol, as_of, benchmark=benchmark)
    return {
        "symbol": result["symbol"],
        "as_of": result["as_of"],
        "alerts": result["alerts"],
        "signal_score": result["score"],
        "regime": result["regime"],
    }


@mcp.tool()
async def analyze_chip_flow(
    symbol: str,
    as_of: str,
    strategy_version: str = "chip_flow_v1",
) -> dict[str, Any]:
    """Analyze official institutional flows and optional licensed branch activity."""
    async with session_factory() as session:
        snapshot = await ChipFlowService(
            PostgresQuantRepository(session)
        ).analyze(
            symbol.strip().upper(),
            as_of=date.fromisoformat(as_of),
            strategy_version=strategy_version,
        )
        return snapshot.model_dump(mode="json")


@mcp.tool()
async def start_backtest(
    symbol: str,
    start: str,
    end: str,
    benchmark: str | None = None,
    market: str | None = "TW",
    strategy_version: str = "regime_v1",
    entry_score: float = 70,
    exit_score: float = 45,
    fee_bps: float = 5,
    slippage_bps: float = 5,
    use_chip_filter: bool = False,
    chip_entry_score: float = 55,
    chip_exit_score: float = 40,
) -> dict[str, Any]:
    """Queue a durable next-open long/cash backtest and return its job ID."""
    spec = BacktestSpec(
        symbol=symbol.strip().upper(),
        benchmark=benchmark.strip().upper() if benchmark else None,
        market=market.strip().upper() if market else None,
        strategy_version=strategy_version,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        entry_score=entry_score,
        exit_score=exit_score,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        use_chip_filter=use_chip_filter,
        chip_entry_score=chip_entry_score,
        chip_exit_score=chip_exit_score,
    )
    async with session_factory() as session:
        job = await BacktestJobService(PostgresQuantRepository(session)).start(spec)
        return job.model_dump(mode="json", exclude={"result"})


@mcp.tool()
async def get_backtest_status(job_id: str) -> dict[str, Any]:
    """Return durable backtest status; completed jobs include headline metrics."""
    async with session_factory() as session:
        job = await BacktestJobService(PostgresQuantRepository(session)).get(UUID(job_id))
        if job is None:
            raise ValueError("backtest job not found")
        payload = job.model_dump(mode="json", exclude={"result"})
        if job.status == JobStatus.COMPLETED and job.result:
            payload["summary"] = job.result.model_dump(
                mode="json",
                exclude={"equity_curve", "spec"},
            )
        return payload


@mcp.tool()
async def get_backtest_report(job_id: str) -> dict[str, Any]:
    """Return the complete report for a finished backtest job."""
    async with session_factory() as session:
        job = await BacktestJobService(PostgresQuantRepository(session)).get(UUID(job_id))
        if job is None:
            raise ValueError("backtest job not found")
        if job.status != JobStatus.COMPLETED or job.result is None:
            return {
                "job_id": str(job.id),
                "status": job.status.value,
                "error": job.error,
            }
        return job.result.model_dump(mode="json")


@mcp.tool()
async def get_analysis_context(
    symbol: str,
    as_of: str,
    market: str = "TW",
    benchmark: str | None = "^TWII",
    strategy_version: str = "regime_v1",
    backtest_job_id: str | None = None,
) -> dict[str, Any]:
    """Build one evidence bundle for an external agent without invoking another LLM."""
    async with session_factory() as session:
        context = await AnalysisContextService(PostgresQuantRepository(session)).build(
            symbol=symbol.strip().upper(),
            as_of=date.fromisoformat(as_of),
            market=market.strip().upper(),
            benchmark=benchmark.strip().upper() if benchmark else None,
            strategy_version=strategy_version,
            backtest_job_id=UUID(backtest_job_id) if backtest_job_id else None,
        )
        return context.model_dump(mode="json")


@mcp.tool()
async def generate_llm_analysis(
    symbol: str,
    as_of: str,
    market: str = "TW",
    benchmark: str | None = "^TWII",
    strategy_version: str = "regime_v1",
    backtest_job_id: str | None = None,
) -> dict[str, Any]:
    """Invoke the configured built-in LLM analyst over quantitative evidence."""
    if settings.llm_errors():
        raise ValueError(f"LLM is not configured: {', '.join(settings.llm_errors())}")
    async with session_factory() as session:
        report = await LLMAnalysisService(
            PostgresQuantRepository(session),
            create_llm_client(settings),
        ).analyze(
            symbol=symbol.strip().upper(),
            as_of=date.fromisoformat(as_of),
            market=market.strip().upper(),
            benchmark=benchmark.strip().upper() if benchmark else None,
            strategy_version=strategy_version,
            backtest_job_id=UUID(backtest_job_id) if backtest_job_id else None,
        )
        return report.model_dump(mode="json")


@mcp.tool()
async def generate_debate_analysis(
    symbol: str,
    as_of: str,
    market: str = "TW",
    benchmark: str | None = "^TWII",
    strategy_version: str = "regime_v1",
    backtest_job_id: str | None = None,
) -> dict[str, Any]:
    """Run a bull/bear debate over quantitative evidence, then a synthesized verdict."""
    if settings.llm_errors():
        raise ValueError(f"LLM is not configured: {', '.join(settings.llm_errors())}")
    async with session_factory() as session:
        report = await DebateAnalysisService(
            PostgresQuantRepository(session),
            create_llm_client(settings),
        ).analyze(
            symbol=symbol.strip().upper(),
            as_of=date.fromisoformat(as_of),
            market=market.strip().upper(),
            benchmark=benchmark.strip().upper() if benchmark else None,
            strategy_version=strategy_version,
            backtest_job_id=UUID(backtest_job_id) if backtest_job_id else None,
        )
        return report.model_dump(mode="json")


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not settings.api_auth_enabled:
            return await call_next(request)
        raw_key = _extract_raw_key(request.headers)
        if not raw_key:
            return JSONResponse({"error": "missing API key"}, status_code=401)
        async with session_factory() as session:
            key = await ApiKeyService(PostgresQuantRepository(session)).verify(raw_key)
        if key is None:
            return JSONResponse({"error": "invalid or revoked API key"}, status_code=401)
        return await call_next(request)


def _extract_raw_key(headers) -> str | None:
    authorization = headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[len("bearer "):].strip()
    x_api_key = headers.get("x-api-key")
    return x_api_key.strip() if x_api_key else None


def main() -> None:
    if settings.mcp_transport == "streamable-http":
        import uvicorn

        app = mcp.streamable_http_app()
        app.add_middleware(ApiKeyAuthMiddleware)
        uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port)
    else:
        mcp.run(transport=settings.mcp_transport)


if __name__ == "__main__":
    main()
