"""Shared pieces for the AI-proposal tests: a fake model, a fixed analysis context."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from conftest import make_bars, make_market_observations
from paper_support import FRI, THU, Clock, at, market_repo, new_user, quote

from quant_signal.application.llm_analysis import AnalysisContextService
from quant_signal.application.paper_jobs import ledger_entries
from quant_signal.application.paper_proposals import PaperProposalService
from quant_signal.application.paper_trading import PaperService
from quant_signal.domain.models import LLMAnalysisContext
from quant_signal.domain.paper import AccountCreate, PaperFillRecord, PaperOrderRecord

SYMBOL = "1101.TW"


class FakeLLM:
    """Canned answers, popped in order (the last one repeats). Never a real model."""

    model_name = "fake-trader"

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict]] = []

    async def complete_json(self, system_prompt: str, payload: dict) -> dict:
        self.calls.append((system_prompt, payload))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return dict(answer)

    async def stream_text(self, system_prompt, payload):  # pragma: no cover - unused
        yield ""


class FixedContext:
    """Stands in for ``AnalysisContextService``: always returns the prebuilt context."""

    def __init__(self, context: LLMAnalysisContext, repository) -> None:
        self.context = context
        self.repository = repository
        self.builds: list[dict] = []

    async def build(self, **kwargs) -> LLMAnalysisContext:
        self.builds.append(kwargs)
        return self.context


def answer(**overrides) -> dict:
    base = {
        "action": "buy",
        "entry_price": 100,
        "stop_loss": 95,
        "take_profit": 110,
        "horizon_days": 20,
        "size_hint": "normal",
        "headline": "量價轉強，回測支撐後分批試單",
        "rationale": "個股訊號轉強且大盤環境健康，進場價貼近參考價，停損設在近期低點下方。",
        "key_evidence": ["個股量化分數高於中性區間"],
        "risks": ["大盤轉弱時須縮手"],
        "invalidation": ["收盤跌破 95"],
        "evidence_refs": ["symbol_signal", "market_environment"],
        "confidence": "medium",
    }
    base.update(overrides)
    return base


async def real_context(repo, symbol: str = SYMBOL) -> LLMAnalysisContext:
    """A genuine analysis context (signal, market environment) built from fixture data."""
    start = THU - timedelta(days=455)
    await repo.upsert_bars(make_bars(symbol=symbol, start=start, count=320))
    await repo.upsert_market_observations(make_market_observations(start=start, count=320))
    return await AnalysisContextService(repo).build(
        symbol=symbol,
        as_of=THU,
        market="TW",
        benchmark="^TWII",
        strategy_version="regime_v1",
    )


async def give_position(repo, account_id, *, lots: int, price="100", symbol=SYMBOL, when=None):
    """Book a filled buy straight into the ledger (no matcher)."""
    when = when or at(THU, 10, 0)
    shares = lots * 1000
    order = PaperOrderRecord(
        account_id=account_id,
        symbol=symbol,
        name="台泥",
        side="buy",
        order_type="market",
        lots=lots,
        session_date=when.date(),
        created_at=when,
        updated_at=when,
    )
    await repo.add_paper_order(order)
    amount = int(Decimal(price) * shares)
    fill = PaperFillRecord(
        order_id=order.id,
        account_id=account_id,
        symbol=symbol,
        side="buy",
        shares=shares,
        price=Decimal(price),
        amount=amount,
        commission=int(amount * Decimal("0.001425")),
        tax=0,
        filled_at=when,
        price_source="tick",
        price_time=when,
    )
    assert await repo.apply_paper_fill(fill, ledger_entries(fill, when), now=when)


class Harness:
    def __init__(self, repo, clock, paper, service, llm, context, user, account_id) -> None:
        self.repo, self.clock, self.paper = repo, clock, paper
        self.service, self.llm, self.context = service, llm, context
        self.user, self.account_id = user, account_id

    async def create(self, symbol: str = SYMBOL):
        return await self.service.create(self.user, self.account_id, symbol)


async def harness(*answers, now=None, initial_cash: int = 1_000_000, price: float = 100.0):
    repo = await market_repo()
    repo.quotes_latest[SYMBOL] = quote(SYMBOL, price, at(FRI, 9, 59))
    context = await real_context(repo)
    clock = Clock(now or at(FRI, 10, 0))
    paper = PaperService(repo, clock=clock)
    user = new_user()
    summary = await paper.create_account(
        user, AccountCreate(name="主帳戶", initial_cash=initial_cash)
    )
    llm = FakeLLM(*(answers or (answer(),)))
    service = PaperProposalService(paper, repo, llm, context_service=FixedContext(context, repo))
    return Harness(repo, clock, paper, service, llm, context, user, summary.id)
