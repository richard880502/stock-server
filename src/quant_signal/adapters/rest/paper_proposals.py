"""REST routes for AI trade proposals (AI 提案), under ``/api/v1/paper``.

Signed-in users only, like every paper route. A proposal is advice for a *simulated*
account; accepting one is placing the order through ``POST .../orders`` with
``proposal_id`` (see ``adapters/rest/paper.py``). Nothing here places an order.
"""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query

from quant_signal.adapters.rest.paper import _user_id
from quant_signal.adapters.rest.security import Principal, enforce_quota, require_principal
from quant_signal.application.llm_analysis import create_llm_client
from quant_signal.application.paper_ports import PaperRepository
from quant_signal.application.paper_proposals import PaperProposalService
from quant_signal.application.paper_trading import PaperService
from quant_signal.domain.paper_proposals import (
    Proposal,
    ProposalCreate,
    ProposalList,
    ProposalReject,
)
from quant_signal.settings import Settings, get_settings


def install_paper_proposals(app: FastAPI, settings: Settings, get_repository: Callable) -> None:
    """Mount the routes next to the paper routes (and only when paper trading is on)."""
    if not settings.paper_trading_enabled:
        return

    router = APIRouter(prefix="/api/v1/paper", dependencies=[Depends(require_principal)])
    User = Annotated[Principal, Depends(require_principal)]

    def _service(repository: PaperRepository, client=None) -> PaperProposalService:
        live = get_settings()
        paper = PaperService(
            repository,
            slippage_ticks=live.paper_slippage_ticks,
            max_accounts=live.paper_max_accounts,
        )
        return PaperProposalService(
            paper,
            repository,
            client,
            max_position_pct=live.paper_proposal_max_position_pct,
            max_risk_pct=live.paper_proposal_max_risk_pct,
            auto_sync=True,
        )

    async def reader(
        repository: Annotated[PaperRepository, Depends(get_repository)],
    ) -> PaperProposalService:
        return _service(repository)

    Reader = Annotated[PaperProposalService, Depends(reader)]

    async def creation_allowed(principal: User) -> None:
        """Runs before the quota dependency, so a refused request never uses a quota slot."""
        _user_id(principal)
        live = get_settings()
        if not live.paper_proposals_enabled:
            raise HTTPException(status_code=503, detail="AI 提案功能目前已關閉")
        if live.llm_errors():
            raise HTTPException(
                status_code=503,
                detail=f"LLM is not configured: {', '.join(live.llm_errors())}",
            )

    @router.post(
        "/accounts/{account_id}/proposals",
        response_model=Proposal,
        status_code=201,
        dependencies=[Depends(creation_allowed), Depends(enforce_quota("llm"))],
    )
    async def create_proposal(
        account_id: UUID,
        body: ProposalCreate,
        principal: User,
        repository: Annotated[PaperRepository, Depends(get_repository)],
    ):
        service = _service(repository, create_llm_client(get_settings()))
        try:
            return await service.create(_user_id(principal), account_id, body.symbol)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/accounts/{account_id}/proposals", response_model=ProposalList)
    async def list_proposals(
        account_id: UUID,
        principal: User,
        svc: Reader,
        status: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        return await svc.list_proposals(
            _user_id(principal), account_id, status=status, limit=limit, offset=offset
        )

    @router.get("/accounts/{account_id}/proposals/{proposal_id}", response_model=Proposal)
    async def get_proposal(account_id: UUID, proposal_id: UUID, principal: User, svc: Reader):
        return await svc.get(_user_id(principal), account_id, proposal_id)

    @router.post("/accounts/{account_id}/proposals/{proposal_id}/reject", response_model=Proposal)
    async def reject_proposal(
        account_id: UUID,
        proposal_id: UUID,
        body: ProposalReject,
        principal: User,
        svc: Reader,
    ):
        return await svc.reject(_user_id(principal), account_id, proposal_id, body.reason)

    app.include_router(router)
