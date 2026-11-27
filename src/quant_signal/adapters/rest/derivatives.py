"""REST routes for the TAIFEX derivatives feed (installed with one line in ``main.py``).

No ``from __future__ import annotations`` here: FastAPI must see the ``Depends`` of the
repository provider, which is a closure variable, when the route is defined.
"""

from collections.abc import Callable
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from quant_signal.adapters.rest.security import require_principal
from quant_signal.application.derivatives_queries import (
    MarketDerivativesService,
    StockFuturesService,
)
from quant_signal.application.ports import QuantRepository
from quant_signal.application.stocks import StockNotFound
from quant_signal.domain.derivatives import MarketDerivatives, StockFutures


def install_derivatives(app, get_repository: Callable) -> None:
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_principal)])

    @router.get("/market/derivatives", response_model=MarketDerivatives)
    async def market_derivatives(
        repository: Annotated[QuantRepository, Depends(get_repository)],
        as_of: Annotated[date | None, Query()] = None,
        days: Annotated[int, Query(ge=1, le=260)] = 60,
    ) -> MarketDerivatives:
        """Official TAIFEX positioning: foreign index-futures net open interest, options
        put/call ratios, large-trader concentration and VIX, point-in-time as of ``as_of``."""
        return await MarketDerivativesService(repository).get(
            as_of=as_of or date.today(), days=days
        )

    @router.get("/stocks/{symbol}/futures", response_model=StockFutures)
    async def stock_futures(
        symbol: str,
        repository: Annotated[QuantRepository, Depends(get_repository)],
        as_of: Annotated[date | None, Query()] = None,
        days: Annotated[int, Query(ge=1, le=260)] = 60,
    ) -> StockFutures:
        """Single-stock futures open interest, volume and near-month basis for one stock."""
        try:
            return await StockFuturesService(repository).get(
                symbol, as_of=as_of or date.today(), days=days
            )
        except StockNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    app.include_router(router)
