"""REST route for paper-trading dividends (除權息), added to the paper router."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from quant_signal.domain.paper_actions import CorporateActionList


def install_paper_actions(router: APIRouter, user, service, user_id) -> None:
    """``user`` / ``service`` are the paper router's dependency annotations."""

    @router.get("/accounts/{account_id}/corporate-actions", response_model=CorporateActionList)
    async def list_corporate_actions(
        account_id: UUID,
        principal: user,
        svc: service,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        include_archived: bool = False,
    ):
        return await svc.list_corporate_actions(
            user_id(principal),
            account_id,
            limit=limit,
            offset=offset,
            include_archived=include_archived,
        )
