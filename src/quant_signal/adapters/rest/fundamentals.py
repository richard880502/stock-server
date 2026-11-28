"""REST routes for valuation, revenue trend and quarterly earnings."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from quant_signal.adapters.rest.security import require_principal
from quant_signal.application.fundamentals_queries import StockFundamentalsService
from quant_signal.application.ports import QuantRepository
from quant_signal.application.stocks import StockNotFound
from quant_signal.domain.fundamentals import StockFundamentals


def install_fundamentals(app) -> None:
    # No ``from __future__ import annotations`` here: FastAPI must see the real
    # ``Depends(get_repository)`` object, which is imported inside the function because
    # main imports this module while it is still being defined.
    from quant_signal.adapters.rest.main import get_repository

    router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_principal)])

    @router.get("/stocks/{symbol}/fundamentals", response_model=StockFundamentals)
    async def stock_fundamentals(
        symbol: str,
        repository: Annotated[QuantRepository, Depends(get_repository)],
        as_of: Annotated[date | None, Query()] = None,
    ) -> StockFundamentals:
        """Official valuation (P/E, P/B, yield with 250-day percentiles), monthly revenue
        trend and quarterly earnings, point-in-time as of ``as_of``."""
        try:
            return await StockFundamentalsService(repository).get(
                symbol, as_of=as_of or date.today()
            )
        except StockNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    app.include_router(router)
