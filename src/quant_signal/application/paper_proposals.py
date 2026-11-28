"""AI trade proposals (AI 提案) for paper trading.

The model suggests a direction, a limit price, protective levels and a size *hint*; the
deterministic layer in ``quant/paper_proposal.py`` snaps, sizes and checks it. Nothing is
placed from here: the user accepts a proposal by placing the order themselves (see
``PaperService.place_order`` with ``proposal_id``). A simulation only, no broker call.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4

from annotated_types import MaxLen
from pydantic import BaseModel, Field, StringConstraints

from quant_signal.application.llm_analysis import (
    NEWS_GUARD,
    OFFICIAL_GUARD,
    AnalysisContextService,
    LLMClient,
    LLMProviderError,
    news_evidence_id,
    prompt_context,
    select_news_items,
    validate_llm_output,
)
from quant_signal.application.official_evidence import BLOCK_KEYS
from quant_signal.application.paper_market import MarketView, price_info
from quant_signal.application.paper_ports import PaperRepository
from quant_signal.application.paper_trading import (
    PaperConflict,
    PaperInvalid,
    PaperNotFound,
    PaperService,
    Valuation,
    _reserved_sells,
)
from quant_signal.domain.models import LLMAnalysisContext
from quant_signal.domain.paper import PaperAccountRecord
from quant_signal.domain.paper_proposals import (
    Proposal,
    ProposalCheck,
    ProposalList,
    ProposalOrder,
    ProposalRecord,
    ProposalSignal,
    ProposalSizing,
)
from quant_signal.domain.tw_trading import BOARD_LOT, DEFAULT_COSTS, price_limit_pct_for, tick_size
from quant_signal.quant.paper_calendar import SESSION_CLOSE, TAIPEI, session_for_order
from quant_signal.quant.paper_matching import (
    limits_for_symbol,
    resolve_reference,
    symbol_is_etf,
    to_money,
)
from quant_signal.quant.paper_proposal import (
    AccountState,
    Check,
    Limits,
    MarketRules,
    ModelPlan,
    normalize,
)

log = logging.getLogger(__name__)
D = Decimal

TRADE_PROPOSAL_PROMPT_VERSION = "trade_proposal_v1"
CONTEXT_MARKET = "TW"
CONTEXT_BENCHMARK = "^TWII"
CONTEXT_STRATEGY = "regime_v1"
LIST_STATUSES = frozenset({"pending", "accepted", "rejected", "expired", "superseded"})

TRADE_PROPOSAL_PROMPT = (
    """你是一位以風險管理為優先的量化交易研究員，正在為一個「模擬交易」帳戶
提出單一股票的交易計畫。這是模擬，不會送出任何真實委託；你的計畫會先經程式檢查與計算張數，
再由使用者決定是否採用。
你會收到：由確定性程式計算的量化證據（市場、個股訊號、籌碼、新聞、官方資料區塊等）、
account（帳戶權益、可用資金、此股目前持有狀況）與 trading_rules（參考價、漲跌停價、升降單位、
每張股數、成本摘要）。

規則：
1. 每一項論點都必須建立在輸入 JSON 的證據上，不得補造新聞、價格、基本面或即時資訊。
2. 「hold」（觀望）是完全有效且常見的答案：證據混合、量化訊號為 avoid／reduce／neutral
   而沒有強烈反證、或沒有明確進場條件時，應選 hold，不要為了給出計畫而勉強進場。
3. 「sell」只在 account.position 顯示持有此股時才有意義；沒有持股就不能賣出，請改選 hold。
4. 若 data_sufficient 為 false，必須選 hold。
5. 價格必須與 trading_rules 相稱：entry_price 是限價，需落在漲跌停價之間且貼近參考價；
   買進時 stop_loss < entry_price < take_profit。價格不確定時填 null。
   你不需要自己對齊升降單位，程式會調整。
6. 你不決定張數或金額，只用 size_hint 表達相對規模：small（試單）、normal、full（信心高、風險低）。
   hold 時 entry_price、stop_loss、take_profit、size_hint 填 null。
7. 清楚寫出 invalidation（使這個計畫失效的具體條件）與 risks；與量化訊號方向相反時，
   必須在 rationale 說明理由並把 confidence 降一級。
8. 使用繁體中文；每個陣列最多 8 項，依重要性由高到低排序。
9. evidence_refs 只能引用輸入 evidence 與 news_items 內存在的 id。
10. 只輸出符合下列結構的 JSON，不要 Markdown：
{
  "action": "buy|sell|hold",
  "entry_price": 數字或 null,
  "stop_loss": 數字或 null,
  "take_profit": 數字或 null,
  "horizon_days": 預計持有天數（整數）或 null,
  "size_hint": "small|normal|full" 或 null,
  "headline": "一句話結論",
  "rationale": "完整理由，連結證據與計畫",
  "key_evidence": ["證據"],
  "risks": ["風險"],
  "invalidation": ["失效條件"],
  "evidence_refs": ["證據 id"],
  "confidence": "low|medium|high"
}
"""
    + NEWS_GUARD
    + OFFICIAL_GUARD
)

Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Price = Annotated[float, Field(gt=0, lt=100_000)]


class TradeProposalDraft(BaseModel):
    """The model's answer. No lots and no money amounts: sizing is the program's job."""

    action: Literal["buy", "sell", "hold"]
    entry_price: Price | None = None
    stop_loss: Price | None = None
    take_profit: Price | None = None
    horizon_days: Annotated[int, Field(ge=1, le=365)] | None = None
    size_hint: Literal["small", "normal", "full"] | None = None
    headline: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    rationale: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500)
    ]
    key_evidence: Annotated[list[Line], MaxLen(8)] = Field(default_factory=list)
    risks: Annotated[list[Line], MaxLen(8)] = Field(default_factory=list)
    invalidation: Annotated[list[Line], MaxLen(8)] = Field(default_factory=list)
    evidence_refs: Annotated[list[str], MaxLen(8)] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"


def _dec(value: float | None) -> Decimal | None:
    return None if value is None else D(str(value))


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


def proposal_expiry(now: datetime) -> datetime:
    """13:30 Taipei of the session an order created ``now`` would belong to."""
    day, _ = session_for_order(now)
    return datetime.combine(day, SESSION_CLOSE, TAIPEI).astimezone(UTC)


def evidence_ids(context: LLMAnalysisContext, news_ids: list[str]) -> set[str]:
    """The ids the model may cite: the same ones the debate analysis exposes."""
    ids = {
        key
        for key in (
            "symbol_signal",
            "market_environment",
            "chip_flow",
            "news_sentiment",
            "backtest",
        )
        if getattr(context, key) is not None
    }
    ids.update(key for key in BLOCK_KEYS if getattr(context, key) is not None)
    ids.update(news_ids)
    return ids


class PaperProposalService:
    def __init__(
        self,
        paper: PaperService,
        repo: PaperRepository,
        client: LLMClient | None,
        *,
        max_position_pct: float = 0.20,
        max_risk_pct: float = 0.02,
        auto_sync: bool = False,
        context_service: AnalysisContextService | None = None,
    ) -> None:
        self.paper = paper
        self.repo = repo  # also a PaperProposalRepository (duck-typed on both twins)
        self.client = client
        self.limits = Limits(D(str(max_position_pct)), D(str(max_risk_pct)))
        self.context_service = context_service or AnalysisContextService(
            repo,  # type: ignore[arg-type]
            auto_sync=auto_sync,
        )

    # --- create --------------------------------------------------------------------

    async def create(self, user_id: UUID, account_id: UUID, raw_symbol: str) -> Proposal:
        if self.client is None:
            raise PaperInvalid("LLM 尚未設定，無法產生提案")
        account = await self.paper._own(user_id, account_id)
        symbol = await self.paper._normalize(raw_symbol)
        now = self.paper.clock()
        views = await self.paper._market_for([account], extra=[symbol])
        view = views.get(symbol)
        if view is None or not view.known or view.official_date is None:
            raise PaperNotFound(f"找不到股票 {raw_symbol.strip().upper()}")
        valuation = await self.paper._value(account, views, now)
        session_date, _ = session_for_order(now)
        rules = self._rules(symbol, view, session_date, now)
        as_of = view.official_date

        context = await self.context_service.build(
            symbol=symbol,
            as_of=as_of,
            market=CONTEXT_MARKET,
            benchmark=CONTEXT_BENCHMARK,
            strategy_version=CONTEXT_STRATEGY,
        )
        news_items = await select_news_items(self.context_service.repository, symbol, as_of)
        valid_refs = evidence_ids(context, [news_evidence_id(item) for item in news_items])
        data_sufficient = context.market_environment.latest.source != "synthetic_demo"

        state = self._account_state(account, valuation, symbol, session_date)
        payload = prompt_context(context, news_items)
        payload["evidence"] = sorted(valid_refs)
        payload["as_of"] = as_of.isoformat()
        payload["data_sufficient"] = data_sufficient
        payload["account"] = self._account_block(valuation, symbol, state)
        payload["trading_rules"] = self._rules_block(account, rules, session_date)
        # The reads above opened a transaction on the request's session. End it before the
        # model call (up to the LLM timeout, twice) so no pooled connection sits "idle in
        # transaction" meanwhile, then re-check what may have changed during the call.
        await self.repo.release_read_transaction()  # type: ignore[attr-defined]
        draft, raw = await self._ask(payload)
        current = await self.paper._own(user_id, account_id)  # 404 if deleted or archived
        if current.epoch != account.epoch:
            raise PaperConflict("模擬帳戶在產生提案期間已重設，請重新產生提案")
        draft.evidence_refs = [ref for ref in draft.evidence_refs if ref in valid_refs]

        notes: list[Check] = []
        if not data_sufficient:
            draft = self._force_hold(draft)
            notes.append(
                Check(
                    "data_insufficient",
                    "info",
                    "市場資料為示範資料或不足，無法形成可靠的交易計畫，已改為觀望",
                )
            )
        plan = ModelPlan(
            action=draft.action,
            entry_price=_dec(draft.entry_price),
            stop_loss=_dec(draft.stop_loss),
            take_profit=_dec(draft.take_profit),
            size_hint=draft.size_hint,
        )
        signal = context.symbol_signal
        result = normalize(
            plan,
            rules=rules,
            account=state,
            limits=self.limits,
            signal_action=signal.action.value,
        )
        checks = [*notes, *result.checks]
        sizing = ProposalSizing(
            equity=valuation.equity,
            cash_available=state.available_cash,
            position_lots=state.held_shares // BOARD_LOT,
            risk_amount=result.risk_amount,
            risk_pct=result.risk_pct,
            position_pct=result.position_pct,
        )
        served = {
            "stop_loss": _f(result.stop_loss),
            "take_profit": _f(result.take_profit),
            "horizon_days": draft.horizon_days,
            "headline": draft.headline,
            "rationale": draft.rationale,
            "key_evidence": draft.key_evidence,
            "risks": draft.risks,
            "invalidation": draft.invalidation,
            "evidence_refs": draft.evidence_refs,
            "confidence": draft.confidence,
            "signal": ProposalSignal(
                action=signal.action.value, score=round(signal.score, 1), as_of=signal.as_of
            ).model_dump(mode="json"),
            "aligned": result.aligned,
            "checks": [c.as_dict() for c in checks],
            "sizing": sizing.model_dump(mode="json"),
            "reference_price": float(rules.reference),
            "model": self.client.model_name,
            "prompt_version": TRADE_PROPOSAL_PROMPT_VERSION,
            "est_amount": result.est_amount,
            "raw_model_output": raw,
        }
        record = ProposalRecord(
            id=uuid4(),
            user_id=user_id,
            account_id=account.id,
            epoch=account.epoch,
            symbol=symbol,
            name=view.name or symbol,
            as_of=as_of,
            created_at=now,
            expires_at=proposal_expiry(now),
            status="pending",
            action=draft.action,
            side=result.side if result.has_order else None,
            limit_price=result.entry_price if result.has_order else None,
            lots=result.lots if result.has_order else None,
            payload=served,
        )
        await self.repo.add_paper_proposal(record)  # type: ignore[attr-defined]
        return self._model(record, account.epoch, now)

    async def _ask(self, payload: dict) -> tuple[TradeProposalDraft, dict]:
        """One model call; an answer that fails validation is asked for once more."""
        assert self.client is not None
        last: LLMProviderError | None = None
        for _attempt in range(2):
            raw = await self.client.complete_json(TRADE_PROPOSAL_PROMPT, payload)
            try:
                return validate_llm_output(TradeProposalDraft, raw), raw
            except LLMProviderError as exc:
                last = exc
                log.warning("trade proposal answer failed validation: %s", exc)
        assert last is not None
        raise last

    @staticmethod
    def _force_hold(draft: TradeProposalDraft) -> TradeProposalDraft:
        return draft.model_copy(
            update={
                "action": "hold",
                "entry_price": None,
                "stop_loss": None,
                "take_profit": None,
                "size_hint": None,
                "horizon_days": None,
                "headline": "資料不足，維持觀望",
                "rationale": (
                    "市場資料為示範資料或不足，無法形成可靠的交易計畫；"
                    "待真實市場資料可用後再重新提案。"
                ),
                "key_evidence": [],
                "invalidation": [],
                "confidence": "low",
            }
        )

    def _rules(
        self, symbol: str, view: MarketView, session_date: date, now: datetime
    ) -> MarketRules:
        quote = view.quote
        reference = resolve_reference(
            session_date,
            quote_reference=D(str(quote.reference)) if quote and quote.reference else None,
            quote_price=D(str(quote.price)) if quote else None,
            quote_date=view.quote_date,
            official_close=view.official_close,
            official_date=view.official_date,
        )
        info = price_info(view, now)
        if reference is None:
            reference = info.price if info else view.official_close
        if reference is None:
            raise PaperInvalid("查無參考價，暫時無法產生提案，請稍後再試")
        down = up = None
        if price_limit_pct_for(symbol) is not None:
            down, up = limits_for_symbol(reference, symbol)
        return MarketRules(
            reference=reference,
            etf=symbol_is_etf(symbol),
            limit_down=down,
            limit_up=up,
            mark=info.price if info else None,
        )

    @staticmethod
    def _account_state(
        account: PaperAccountRecord, valuation: Valuation, symbol: str, session_date: date
    ) -> AccountState:
        held = valuation.book.held(symbol)
        reserved = _reserved_sells(valuation.working).get(symbol, 0)
        locked = 0 if account.allow_day_trade else valuation.book.bought_on(symbol, session_date)
        return AccountState(
            equity=valuation.equity,
            available_cash=valuation.cash - valuation.reserved,
            commission_discount=account.commission_discount,
            held_shares=held,
            sellable_shares=max(0, held - reserved - locked),
            symbol=symbol,
        )

    @staticmethod
    def _account_block(valuation: Valuation, symbol: str, state: AccountState) -> dict:
        position = next((p for p in valuation.positions if p.symbol == symbol), None)
        return {
            "equity": valuation.equity,
            "available_cash": state.available_cash,
            "position": None
            if position is None
            else {
                "lots": position.lots,
                "avg_cost": position.avg_cost,
                "unrealized_pct": position.unrealized_pct,
            },
        }

    @staticmethod
    def _rules_block(account: PaperAccountRecord, rules: MarketRules, session_date: date) -> dict:
        costs = DEFAULT_COSTS
        discount = float(account.commission_discount)
        return {
            "session_date": session_date.isoformat(),
            "reference_price": float(rules.reference),
            "limit_up": _f(rules.limit_up),
            "limit_down": _f(rules.limit_down),
            "tick_size": float(tick_size(rules.reference, etf=rules.etf)),
            "board_lot": BOARD_LOT,
            "costs": (
                f"手續費 {costs.commission_rate * 100:.4f}% 乘折數 {discount:g}、每筆最低 "
                f"NT${costs.min_commission}；賣出證交稅股票 {costs.stock_tax_rate * 100:.1f}%、"
                f"ETF {costs.etf_tax_rate * 100:.1f}%"
            ),
        }

    # --- read / decide -------------------------------------------------------------

    def _model(self, record: ProposalRecord, epoch: int, now: datetime) -> Proposal:
        data = record.payload
        status = record.status
        if status == "pending" and (record.expires_at <= now or record.epoch != epoch):
            status = "expired"  # shown as expired even before the next write persists it
        order = None
        if record.side is not None and record.lots is not None and record.limit_price is not None:
            order = ProposalOrder(
                side=record.side,
                limit_price=float(record.limit_price),
                lots=record.lots,
                est_amount=int(data.get("est_amount") or 0)
                or to_money(D(record.lots * BOARD_LOT) * record.limit_price),
            )
        return Proposal(
            id=record.id,
            account_id=record.account_id,
            symbol=record.symbol,
            name=record.name,
            as_of=record.as_of,
            created_at=_utc(record.created_at),
            expires_at=_utc(record.expires_at),
            status=status,
            action=record.action,
            order=order,
            stop_loss=data.get("stop_loss"),
            take_profit=data.get("take_profit"),
            horizon_days=data.get("horizon_days"),
            headline=data["headline"],
            rationale=data["rationale"],
            key_evidence=data.get("key_evidence", []),
            risks=data.get("risks", []),
            invalidation=data.get("invalidation", []),
            evidence_refs=data.get("evidence_refs", []),
            confidence=data["confidence"],
            signal=ProposalSignal(**data["signal"]),
            aligned=data["aligned"],
            checks=[ProposalCheck(**c) for c in data.get("checks", [])],
            sizing=ProposalSizing(**data["sizing"]),
            reference_price=data["reference_price"],
            model=data["model"],
            prompt_version=data["prompt_version"],
            order_id=record.order_id,
            decided_at=None if record.decided_at is None else _utc(record.decided_at),
            reject_reason=record.reject_reason,
        )

    async def list_proposals(
        self,
        user_id: UUID,
        account_id: UUID,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ProposalList:
        account = await self.paper._own(user_id, account_id)
        now = self.paper.clock()
        await self.repo.expire_paper_proposals(account.id, now=now)  # type: ignore[attr-defined]
        if status is not None and status not in LIST_STATUSES:
            raise PaperInvalid("status 必須是 pending、accepted、rejected、expired 或 superseded")
        records, total = await self.repo.list_paper_proposals(  # type: ignore[attr-defined]
            account.id,
            epoch=account.epoch,
            statuses=None if status is None else [status],
            limit=limit,
            offset=offset,
        )
        return ProposalList(
            proposals=[self._model(r, account.epoch, now) for r in records],
            has_more=offset + len(records) < total,
        )

    async def _own_proposal(
        self, user_id: UUID, account_id: UUID, proposal_id: UUID
    ) -> tuple[PaperAccountRecord, ProposalRecord, datetime]:
        account = await self.paper._own(user_id, account_id)
        now = self.paper.clock()
        await self.repo.expire_paper_proposals(account.id, now=now)  # type: ignore[attr-defined]
        record = await self.repo.get_paper_proposal(proposal_id)  # type: ignore[attr-defined]
        if record is None or record.account_id != account.id or record.user_id != user_id:
            raise PaperNotFound("找不到這個提案")
        return account, record, now

    async def get(self, user_id: UUID, account_id: UUID, proposal_id: UUID) -> Proposal:
        account, record, now = await self._own_proposal(user_id, account_id, proposal_id)
        return self._model(record, account.epoch, now)

    async def reject(
        self, user_id: UUID, account_id: UUID, proposal_id: UUID, reason: str | None
    ) -> Proposal:
        account, record, now = await self._own_proposal(user_id, account_id, proposal_id)
        reason = (reason or "").strip() or None
        if record.status != "pending" or record.epoch != account.epoch:
            raise PaperConflict(_NOT_PENDING.get(record.status, "此提案已結束，無法拒絕"))
        if not await self.repo.reject_paper_proposal(  # type: ignore[attr-defined]
            record.id, now=now, reason=reason
        ):
            current = await self.repo.get_paper_proposal(record.id)  # type: ignore[attr-defined]
            raise PaperConflict(
                _NOT_PENDING.get(current.status if current else "", "此提案已結束，無法拒絕")
            )
        updated = await self.repo.get_paper_proposal(record.id)  # type: ignore[attr-defined]
        assert updated is not None
        return self._model(updated, account.epoch, now)


_NOT_PENDING = {
    "accepted": "此提案已採用",
    "rejected": "此提案已拒絕",
    "expired": "此提案已過期",
    "superseded": "此提案已被同一檔股票的新提案取代",
}
