"""REST route for short-lending / day-trade / pledge / dividend data."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from quant_signal.adapters.rest.security import require_principal
from quant_signal.application.ports import QuantRepository
from quant_signal.application.short_data_queries import StockShortInterestService
from quant_signal.application.stocks import StockNotFound
from quant_signal.domain.short_data import StockShortInterest


def install_short_data(app, get_repository) -> None:
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_principal)])

    @router.get("/stocks/{symbol}/short-interest", response_model=StockShortInterest)
    async def stock_short_interest(
        symbol: str,
        repository: Annotated[QuantRepository, Depends(get_repository)],
        as_of: Annotated[date | None, Query()] = None,
        days: Annotated[int, Query(ge=1, le=260)] = 60,
    ) -> StockShortInterest:
        """借券賣出、當沖比、董監質設與近 60 日除權息，皆為 as_of 當時已公開的資料。"""
        try:
            return await StockShortInterestService(repository).get(
                symbol, as_of=as_of or date.today(), days=days
            )
        except StockNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    app.include_router(router)
