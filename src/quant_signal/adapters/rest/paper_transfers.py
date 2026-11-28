"""REST routes for paper-account deposits and withdrawals, added to the paper router."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from quant_signal.domain.paper import AccountSummary, TransferList, TransferRequest


def install_paper_transfers(router: APIRouter, user, service, user_id) -> None:
    """``user`` / ``service`` are the paper router's dependency annotations."""

    @router.post("/accounts/{account_id}/transfers", response_model=AccountSummary)
    async def transfer(account_id: UUID, body: TransferRequest, principal: user, svc: service):
        return await svc.transfer(user_id(principal), account_id, body)

    @router.get("/accounts/{account_id}/transfers", response_model=TransferList)
    async def list_transfers(
        account_id: UUID,
        principal: user,
        svc: service,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        include_archived: bool = False,
    ):
        return await svc.list_transfers(
            user_id(principal),
            account_id,
            limit=limit,
            offset=offset,
            include_archived=include_archived,
        )
