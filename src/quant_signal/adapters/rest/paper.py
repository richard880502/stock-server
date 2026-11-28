"""REST routes for paper trading (模擬交易), mounted at ``/api/v1/paper``.

Signed-in users only (API keys get 403) and every account is private to its owner.
This is a simulation: no route here, or anything it calls, places a real order.
"""

from collections.abc import Callable
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from quant_signal.adapters.rest.security import Principal, require_principal
from quant_signal.application.paper_ports import PaperRepository
from quant_signal.application.paper_trading import (
    PaperConflict,
    PaperInvalid,
    PaperNotFound,
    PaperService,
    paper_rules,
)
from quant_signal.domain.paper import (
    AccountCreate,
    AccountDetail,
    AccountList,
    AccountPatch,
    AccountSummary,
    EquityCurve,
    FillList,
    Order,
    OrderList,
    OrderPreview,
    OrderRequest,
)
from quant_signal.settings import Settings, get_settings


def _user_id(principal: Principal) -> UUID:
    if principal.kind != "user" or principal.user is None:
        raise HTTPException(
            status_code=403, detail="模擬交易需要以 Google 帳號登入（API 金鑰與匿名存取不可使用）"
        )
    return principal.user.id


def install_paper(app: FastAPI, settings: Settings, get_repository: Callable) -> None:
    """Mount the routes unless ``PAPER_TRADING_ENABLED`` is off."""
    if not settings.paper_trading_enabled:
        return

    @app.exception_handler(PaperNotFound)
    async def _not_found(_: Request, exc: PaperNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(PaperConflict)
    async def _conflict(_: Request, exc: PaperConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(PaperInvalid)
    async def _invalid(_: Request, exc: PaperInvalid) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    router = APIRouter(prefix="/api/v1/paper", dependencies=[Depends(require_principal)])

    async def service(
        repository: Annotated[PaperRepository, Depends(get_repository)],
    ) -> PaperService:
        live = get_settings()
        return PaperService(
            repository,
            slippage_ticks=live.paper_slippage_ticks,
            max_accounts=live.paper_max_accounts,
        )

    User = Annotated[Principal, Depends(require_principal)]
    Service = Annotated[PaperService, Depends(service)]

    @router.get("/rules")
    async def rules(principal: User) -> dict:
        _user_id(principal)
        live = get_settings()
        return paper_rules(live.paper_slippage_ticks, live.paper_max_accounts)

    @router.get("/accounts", response_model=AccountList)
    async def list_accounts(principal: User, svc: Service) -> AccountList:
        accounts = await svc.list_accounts(_user_id(principal))
        return AccountList(accounts=accounts, max_accounts=svc.max_accounts)

    @router.post("/accounts", response_model=AccountSummary, status_code=201)
    async def create_account(body: AccountCreate, principal: User, svc: Service):
        return await svc.create_account(_user_id(principal), body)

    @router.get("/accounts/{account_id}", response_model=AccountDetail)
    async def get_account(account_id: UUID, principal: User, svc: Service):
        return await svc.get_account(_user_id(principal), account_id)

    @router.patch("/accounts/{account_id}", response_model=AccountSummary)
    async def patch_account(account_id: UUID, body: AccountPatch, principal: User, svc: Service):
        return await svc.update_account(_user_id(principal), account_id, body)

    @router.post("/accounts/{account_id}/reset", response_model=AccountSummary)
    async def reset_account(account_id: UUID, principal: User, svc: Service):
        return await svc.reset_account(_user_id(principal), account_id)

    @router.delete("/accounts/{account_id}")
    async def delete_account(account_id: UUID, principal: User, svc: Service) -> dict:
        await svc.archive_account(_user_id(principal), account_id)
        return {"archived": True, "id": str(account_id)}

    @router.post("/accounts/{account_id}/orders", response_model=Order | OrderPreview)
    async def place_order(
        account_id: UUID,
        body: OrderRequest,
        principal: User,
        svc: Service,
        preview: bool = False,
    ):
        user_id = _user_id(principal)
        if preview:
            return await svc.preview(user_id, account_id, body)
        return await svc.place_order(user_id, account_id, body)

    @router.get("/accounts/{account_id}/orders", response_model=OrderList)
    async def list_orders(
        account_id: UUID,
        principal: User,
        svc: Service,
        status: Literal["working", "done", "all"] = "all",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        include_archived: bool = False,
    ):
        return await svc.list_orders(
            _user_id(principal),
            account_id,
            status=status,
            limit=limit,
            offset=offset,
            include_archived=include_archived,
        )

    @router.delete("/accounts/{account_id}/orders/{order_id}", response_model=Order)
    async def cancel_order(account_id: UUID, order_id: UUID, principal: User, svc: Service):
        return await svc.cancel_order(_user_id(principal), account_id, order_id)

    @router.get("/accounts/{account_id}/fills", response_model=FillList)
    async def list_fills(
        account_id: UUID,
        principal: User,
        svc: Service,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        include_archived: bool = False,
    ):
        return await svc.list_fills(
            _user_id(principal),
            account_id,
            limit=limit,
            offset=offset,
            include_archived=include_archived,
        )

    @router.get("/accounts/{account_id}/equity", response_model=EquityCurve)
    async def equity(
        account_id: UUID,
        principal: User,
        svc: Service,
        days: Annotated[int, Query(ge=1)] = 120,
    ):
        return await svc.equity(_user_id(principal), account_id, days)

    app.include_router(router)
