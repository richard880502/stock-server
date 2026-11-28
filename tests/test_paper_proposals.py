"""AI trade proposals end to end on the in-memory repository (fake model, no network)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from paper_proposal_support import SYMBOL, answer, give_position, harness
from paper_support import FRI, MON, THU, at, new_user

from quant_signal.application.llm_analysis import LLMProviderError
from quant_signal.application.paper_proposals import (
    TRADE_PROPOSAL_PROMPT,
    TRADE_PROPOSAL_PROMPT_VERSION,
    PaperProposalService,
)
from quant_signal.application.paper_trading import PaperConflict, PaperInvalid, PaperNotFound
from quant_signal.domain.models import SignalAction
from quant_signal.domain.paper import AccountCreate, OrderRequest

D = Decimal


def order_request(proposal, **overrides) -> OrderRequest:
    base = {
        "symbol": proposal.symbol,
        "side": proposal.order.side if proposal.order else "buy",
        "order_type": "limit",
        "limit_price": D(str(proposal.order.limit_price)) if proposal.order else D("100"),
        "lots": proposal.order.lots if proposal.order else 1,
        "proposal_id": proposal.id,
    }
    base.update(overrides)
    return OrderRequest(**base)


# --- creating ---------------------------------------------------------------------


async def test_buy_proposal_is_normalized_and_stored() -> None:
    h = await harness(answer(entry_price=100.04, evidence_refs=["symbol_signal", "made_up", "x:1"]))
    proposal = await h.create()

    assert proposal.status == "pending" and proposal.action == "buy"
    assert proposal.symbol == SYMBOL and proposal.name == "台泥"
    assert proposal.as_of == THU  # the newest official daily row
    assert proposal.reference_price == 100.0
    assert proposal.order is not None
    assert proposal.order.model_dump() == {
        "side": "buy",
        "order_type": "limit",
        "limit_price": 100.0,  # 100.04 snapped down to the 0.5 tick
        "lots": 1,
        "est_amount": 100_000,
    }
    assert proposal.stop_loss == 95.0 and proposal.take_profit == 110.0
    assert proposal.evidence_refs == ["symbol_signal"]  # unknown ids filtered out
    assert proposal.model == "fake-trader"
    assert proposal.prompt_version == TRADE_PROPOSAL_PROMPT_VERSION == "trade_proposal_v1"
    assert [c.code for c in proposal.checks] == ["price_snapped"]
    assert proposal.sizing.equity == 1_000_000 and proposal.sizing.cash_available == 1_000_000
    assert proposal.sizing.position_lots == 0
    assert proposal.sizing.position_pct == 0.1 and proposal.sizing.risk_pct is not None
    # 13:30 Taipei on the session day of creation (Friday 10:00 -> same day)
    assert proposal.expires_at == at(FRI, 13, 30).astimezone(UTC)
    assert proposal.signal.action == h.context.symbol_signal.action.value
    assert proposal.order_id is None and proposal.decided_at is None

    assert await h.service.get(h.user, h.account_id, proposal.id) == proposal
    listing = await h.service.list_proposals(h.user, h.account_id)
    assert [p.id for p in listing.proposals] == [proposal.id] and not listing.has_more


async def test_the_model_sees_evidence_account_and_rules_and_is_called_once() -> None:
    h = await harness(answer())
    await h.create()
    assert len(h.llm.calls) == 1
    prompt, payload = h.llm.calls[0]
    assert prompt == TRADE_PROPOSAL_PROMPT
    assert "hold" in prompt and "不得補造" in prompt and "news_items" in prompt  # guards present
    assert payload["symbol_signal"]["symbol"] == SYMBOL
    assert payload["data_sufficient"] is True
    assert payload["account"] == {
        "equity": 1_000_000,
        "available_cash": 1_000_000,
        "position": None,
    }
    rules = payload["trading_rules"]
    assert rules["reference_price"] == 100.0
    assert rules["limit_up"] == 110.0 and rules["limit_down"] == 90.0
    assert rules["tick_size"] == 0.5 and rules["board_lot"] == 1000
    assert "symbol_signal" in payload["evidence"]
    assert h.service.context_service.builds[0]["as_of"] == THU


async def test_position_is_described_to_the_model() -> None:
    h = await harness(answer(action="hold", entry_price=None, stop_loss=None, take_profit=None))
    await give_position(h.repo, h.account_id, lots=3, price="90")
    await h.create()
    position = h.llm.calls[0][1]["account"]["position"]
    assert position["lots"] == 3 and position["avg_cost"] > 90 and position["unrealized_pct"] > 0


async def test_sell_proposal_sizes_from_the_position() -> None:
    h = await harness(answer(action="sell", entry_price=100, stop_loss=90, take_profit=None))
    await give_position(h.repo, h.account_id, lots=3)
    proposal = await h.create()
    assert proposal.order is not None and proposal.order.side == "sell"
    assert proposal.order.lots == 1  # "normal" sells half of 3 lots, rounded down
    assert proposal.stop_loss is None and proposal.sizing.position_lots == 3
    assert "levels_dropped" in [c.code for c in proposal.checks]


async def test_sell_without_a_position_is_blocked() -> None:
    h = await harness(answer(action="sell", entry_price=100, stop_loss=None, take_profit=None))
    proposal = await h.create()
    assert proposal.order is None and proposal.action == "sell"
    assert [(c.code, c.level) for c in proposal.checks if c.level == "blocked"] == [
        ("blocked_no_position", "blocked")
    ]


async def test_working_sell_orders_reduce_what_can_be_sold() -> None:
    h = await harness(answer(action="sell", entry_price=100, size_hint="full", stop_loss=None))
    await give_position(h.repo, h.account_id, lots=3)
    h.clock.now = at(FRI, 10, 0)
    await h.paper.place_order(
        h.user,
        h.account_id,
        OrderRequest(symbol=SYMBOL, side="sell", order_type="limit", limit_price=D("110"), lots=2),
    )
    proposal = await h.create()
    assert proposal.order is not None and proposal.order.lots == 1
    assert "lots_capped" in [c.code for c in proposal.checks]


async def test_hold_has_no_order() -> None:
    h = await harness(
        answer(action="hold", entry_price=None, stop_loss=None, take_profit=None, size_hint=None)
    )
    proposal = await h.create()
    assert proposal.action == "hold" and proposal.order is None
    assert proposal.stop_loss is None and proposal.aligned
    assert not [c for c in proposal.checks if c.level == "blocked"]


async def test_blocked_buy_keeps_the_plan_but_has_no_order() -> None:
    h = await harness(answer(), initial_cash=100_000)  # one lot (100,000) is 100% of equity
    proposal = await h.create()
    assert proposal.order is None and proposal.action == "buy"
    assert proposal.stop_loss == 95.0 and proposal.take_profit == 110.0
    blocked = [c for c in proposal.checks if c.level == "blocked"]
    assert [c.code for c in blocked] == ["blocked_position_cap"]
    assert "資金不足一張" in blocked[0].message
    assert proposal.sizing.equity == 100_000 and proposal.sizing.risk_amount is None


async def test_misaligned_proposal_is_flagged_but_not_blocked() -> None:
    h = await harness(answer())
    h.context.symbol_signal.action = SignalAction.AVOID
    proposal = await h.create()
    assert proposal.aligned is False and proposal.order is not None
    assert proposal.signal.action == "avoid"
    conflict = next(c for c in proposal.checks if c.code == "signal_conflict")
    assert conflict.level == "info" and "暫停進場" in conflict.message


async def test_insufficient_data_forces_hold() -> None:
    h = await harness(answer(action="buy"))
    latest = h.context.market_environment.latest.model_copy(update={"source": "synthetic_demo"})
    h.context.market_environment = h.context.market_environment.model_copy(
        update={"latest": latest}
    )
    proposal = await h.create()
    assert proposal.action == "hold" and proposal.order is None
    assert proposal.headline == "資料不足，維持觀望" and proposal.confidence == "low"
    assert proposal.stop_loss is None
    assert "data_insufficient" in [c.code for c in proposal.checks]
    assert h.llm.calls[0][1]["data_sufficient"] is False


async def test_invalid_model_output_is_retried_once() -> None:
    h = await harness({"action": "buy"}, answer())
    proposal = await h.create()
    assert len(h.llm.calls) == 2 and proposal.order is not None


async def test_two_invalid_answers_raise_a_provider_error() -> None:
    h = await harness({"action": "buy"}, {"action": "maybe", "headline": "x"})
    with pytest.raises(LLMProviderError):
        await h.create()
    assert len(h.llm.calls) == 2
    assert (await h.service.list_proposals(h.user, h.account_id)).proposals == []


async def test_a_provider_failure_is_not_retried() -> None:
    h = await harness(LLMProviderError("boom"))
    with pytest.raises(LLMProviderError):
        await h.create()
    assert len(h.llm.calls) == 1


async def test_prices_the_model_cannot_name_are_rejected_by_validation() -> None:
    h = await harness(answer(entry_price=-5), answer())
    await h.create()
    assert len(h.llm.calls) == 2


async def test_unknown_symbol_and_unconfigured_model() -> None:
    h = await harness()
    with pytest.raises(PaperNotFound):
        await h.create("9999")
    with pytest.raises(PaperInvalid):
        await h.create("abc")
    unconfigured = PaperProposalService(
        h.paper, h.repo, None, context_service=h.service.context_service
    )
    with pytest.raises(PaperInvalid):
        await unconfigured.create(h.user, h.account_id, SYMBOL)


async def test_accounts_are_private() -> None:
    h = await harness()
    with pytest.raises(PaperNotFound):
        await h.service.create(new_user(), h.account_id, SYMBOL)
    proposal = await h.create()
    stranger = new_user()
    with pytest.raises(PaperNotFound):
        await h.service.get(stranger, h.account_id, proposal.id)
    other = (
        await h.paper.create_account(stranger, AccountCreate(name="別人的", initial_cash=500_000))
    ).id
    with pytest.raises(PaperNotFound):
        await h.service.get(stranger, other, proposal.id)
    mine = (
        await h.paper.create_account(h.user, AccountCreate(name="第二個", initial_cash=500_000))
    ).id
    with pytest.raises(PaperNotFound):  # exists, but belongs to another account
        await h.service.get(h.user, mine, proposal.id)


# --- lifecycle --------------------------------------------------------------------


async def test_a_new_proposal_supersedes_the_pending_one_for_the_same_symbol() -> None:
    h = await harness()
    first = await h.create()
    h.clock.now = at(FRI, 10, 5)
    second = await h.create()
    assert (await h.service.get(h.user, h.account_id, first.id)).status == "superseded"
    assert (await h.service.get(h.user, h.account_id, second.id)).status == "pending"
    pending = await h.service.list_proposals(h.user, h.account_id, status="pending")
    assert [p.id for p in pending.proposals] == [second.id]
    everything = await h.service.list_proposals(h.user, h.account_id)
    assert [p.id for p in everything.proposals] == [second.id, first.id]  # newest first
    page = await h.service.list_proposals(h.user, h.account_id, limit=1)
    assert page.has_more and len(page.proposals) == 1
    with pytest.raises(PaperConflict):
        await h.service.reject(h.user, h.account_id, first.id, None)
    with pytest.raises(PaperInvalid):
        await h.service.list_proposals(h.user, h.account_id, status="nonsense")


async def test_other_symbols_and_accounts_are_not_superseded() -> None:
    h = await harness()
    first = await h.create()
    other = (
        await h.paper.create_account(h.user, AccountCreate(name="第二個", initial_cash=1_000_000))
    ).id
    await h.service.create(h.user, other, SYMBOL)
    assert (await h.service.get(h.user, h.account_id, first.id)).status == "pending"


@pytest.mark.parametrize(
    ("created", "expires"),
    [
        (at(FRI, 10, 0), at(FRI, 13, 30)),
        (at(FRI, 13, 29), at(FRI, 13, 30)),
        (at(FRI, 13, 30), at(MON, 13, 30)),  # after the close: the next session
        (at(FRI, 20, 0), at(MON, 13, 30)),
        (at(THU, 8, 0), at(THU, 13, 30)),  # pre-open on a trading day: that day's close
    ],
)
async def test_expiry_is_the_close_of_the_session_the_order_would_join(created, expires) -> None:
    h = await harness(now=created)
    proposal = await h.create()
    assert proposal.expires_at == expires.astimezone(UTC)


async def test_overdue_pending_proposals_expire_lazily() -> None:
    h = await harness()
    proposal = await h.create()
    h.clock.now = at(FRI, 13, 31)
    assert (await h.service.get(h.user, h.account_id, proposal.id)).status == "expired"
    assert (await h.service.list_proposals(h.user, h.account_id, status="pending")).proposals == []
    expired = await h.service.list_proposals(h.user, h.account_id, status="expired")
    assert [p.id for p in expired.proposals] == [proposal.id]
    stored = await h.repo.get_paper_proposal(proposal.id)
    assert stored.status == "expired" and stored.decided_at is not None
    with pytest.raises(PaperConflict):
        await h.service.reject(h.user, h.account_id, proposal.id, None)


async def test_reject_records_the_reason() -> None:
    h = await harness()
    proposal = await h.create()
    h.clock.now = at(FRI, 10, 20)
    rejected = await h.service.reject(h.user, h.account_id, proposal.id, "  價格不合  ")
    assert rejected.status == "rejected" and rejected.reject_reason == "價格不合"
    assert rejected.decided_at == at(FRI, 10, 20).astimezone(UTC)
    with pytest.raises(PaperConflict):
        await h.service.reject(h.user, h.account_id, proposal.id, None)
    # a rejected proposal can no longer be accepted
    with pytest.raises(PaperConflict):
        await h.paper.place_order(h.user, h.account_id, order_request(proposal))


async def test_reset_expires_pending_proposals() -> None:
    h = await harness()
    proposal = await h.create()
    await h.paper.reset_account(h.user, h.account_id)
    assert (await h.service.get(h.user, h.account_id, proposal.id)).status == "expired"
    assert (await h.service.list_proposals(h.user, h.account_id)).proposals == []  # new epoch
    with pytest.raises(PaperConflict):
        await h.paper.place_order(h.user, h.account_id, order_request(proposal))


# --- accepting --------------------------------------------------------------------


async def test_accepting_places_an_ai_order_and_links_the_proposal() -> None:
    h = await harness()
    proposal = await h.create()
    h.clock.now = at(FRI, 10, 30)
    order = await h.paper.place_order(h.user, h.account_id, order_request(proposal))
    assert order.source == "ai" and order.status == "working"
    assert order.limit_price == 100.0 and order.lots == 1
    accepted = await h.service.get(h.user, h.account_id, proposal.id)
    assert accepted.status == "accepted" and accepted.order_id == order.id
    assert accepted.decided_at == at(FRI, 10, 30).astimezone(UTC)
    orders = await h.paper.list_orders(h.user, h.account_id)
    assert [(o.id, o.source) for o in orders.orders] == [(order.id, "ai")]
    with pytest.raises(PaperConflict):  # one proposal, one order
        await h.paper.place_order(h.user, h.account_id, order_request(proposal))
    assert len((await h.paper.list_orders(h.user, h.account_id)).orders) == 1


async def test_the_users_edited_price_and_lots_win() -> None:
    h = await harness()
    proposal = await h.create()
    order = await h.paper.place_order(
        h.user, h.account_id, order_request(proposal, limit_price=D("99"), lots=2)
    )
    assert order.limit_price == 99.0 and order.lots == 2 and order.source == "ai"
    again = await h.service.get(h.user, h.account_id, proposal.id)
    assert again.status == "accepted"
    assert again.order is not None and again.order.limit_price == 100.0  # advice unchanged


async def test_preview_with_a_proposal_changes_nothing() -> None:
    h = await harness()
    proposal = await h.create()
    preview = await h.paper.preview(h.user, h.account_id, order_request(proposal))
    assert preview.ok
    assert (await h.service.get(h.user, h.account_id, proposal.id)).status == "pending"
    assert (await h.paper.list_orders(h.user, h.account_id)).orders == []
    with pytest.raises(PaperConflict):  # preview validates the proposal too
        await h.paper.preview(h.user, h.account_id, order_request(proposal, side="sell"))


@pytest.mark.parametrize(
    "bad",
    [
        {"side": "sell"},
        {"symbol": "2330.TW"},
    ],
)
async def test_a_mismatched_symbol_or_side_is_a_conflict(bad) -> None:
    h = await harness()
    proposal = await h.create()
    with pytest.raises(PaperConflict):
        await h.paper.place_order(h.user, h.account_id, order_request(proposal, **bad))
    assert (await h.service.get(h.user, h.account_id, proposal.id)).status == "pending"
    assert (await h.paper.list_orders(h.user, h.account_id)).orders == []


async def test_accepting_after_expiry_is_a_conflict() -> None:
    h = await harness()
    proposal = await h.create()
    h.clock.now = at(FRI, 13, 30, 1)
    with pytest.raises(PaperConflict):
        await h.paper.place_order(h.user, h.account_id, order_request(proposal))
    assert (await h.paper.list_orders(h.user, h.account_id)).orders == []


async def test_accepting_on_another_account_or_unknown_id_is_a_conflict() -> None:
    h = await harness()
    proposal = await h.create()
    other = (
        await h.paper.create_account(h.user, AccountCreate(name="第二個", initial_cash=1_000_000))
    ).id
    with pytest.raises(PaperConflict):
        await h.paper.place_order(h.user, other, order_request(proposal))
    from uuid import uuid4

    with pytest.raises(PaperConflict):
        await h.paper.place_order(
            h.user, h.account_id, order_request(proposal, proposal_id=uuid4())
        )


async def test_a_hold_or_blocked_proposal_cannot_be_accepted() -> None:
    h = await harness(answer(action="hold", entry_price=None, stop_loss=None, take_profit=None))
    proposal = await h.create()
    with pytest.raises(PaperConflict):
        await h.paper.place_order(
            h.user,
            h.account_id,
            OrderRequest(
                symbol=SYMBOL,
                side="buy",
                order_type="limit",
                limit_price=D("100"),
                lots=1,
                proposal_id=proposal.id,
            ),
        )


async def test_an_order_the_rules_reject_leaves_the_proposal_pending() -> None:
    h = await harness()
    proposal = await h.create()
    order = await h.paper.place_order(
        h.user, h.account_id, order_request(proposal, lots=500, limit_price=D("100"))
    )
    assert order.status == "rejected" and order.source == "ai"
    assert (await h.service.get(h.user, h.account_id, proposal.id)).status == "pending"
    retry = await h.paper.place_order(h.user, h.account_id, order_request(proposal))
    assert retry.status == "working"


async def test_concurrent_accepts_book_one_order() -> None:
    import asyncio

    h = await harness()
    proposal = await h.create()

    async def accept():
        try:
            return await h.paper.place_order(h.user, h.account_id, order_request(proposal))
        except PaperConflict:
            return None

    results = await asyncio.gather(accept(), accept(), accept())
    assert len([r for r in results if r is not None]) == 1
    assert len((await h.paper.list_orders(h.user, h.account_id)).orders) == 1


def test_prompt_is_in_traditional_chinese_with_the_shared_guards() -> None:
    assert "新聞處理規則" in TRADE_PROPOSAL_PROMPT and "官方資料區塊規則" in TRADE_PROPOSAL_PROMPT
    assert "不會送出任何真實委託" in TRADE_PROPOSAL_PROMPT
    assert isinstance(datetime.now(UTC), datetime)
