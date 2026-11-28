"""The deterministic layer behind AI trade proposals (pure functions, table driven)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from quant_signal.domain.tw_trading import is_on_tick
from quant_signal.quant.paper_proposal import (
    AccountState,
    Limits,
    MarketRules,
    ModelPlan,
    normalize,
)

D = Decimal
LIMITS = Limits(D("0.20"), D("0.02"))


def rules(reference="100", *, etf=False, limited=True, mark=None) -> MarketRules:
    ref = D(reference)
    return MarketRules(
        reference=ref,
        etf=etf,
        limit_down=ref * D("0.9") if limited else None,
        limit_up=ref * D("1.1") if limited else None,
        mark=None if mark is None else D(mark),
    )


def account(
    *, equity=1_000_000, cash=1_000_000, held=0, sellable=None, discount="1.0", symbol="1101.TW"
) -> AccountState:
    return AccountState(
        equity=equity,
        available_cash=cash,
        commission_discount=D(discount),
        held_shares=held,
        sellable_shares=held if sellable is None else sellable,
        symbol=symbol,
    )


def plan(action="buy", entry=None, stop=None, target=None, hint=None) -> ModelPlan:
    return ModelPlan(
        action=action,
        entry_price=None if entry is None else D(entry),
        stop_loss=None if stop is None else D(stop),
        take_profit=None if target is None else D(target),
        size_hint=hint,
    )


def run(p: ModelPlan, *, r=None, a=None, limits=LIMITS, signal="increase"):
    return normalize(
        p, rules=r or rules(), account=a or account(), limits=limits, signal_action=signal
    )


def codes(result, level=None) -> list[str]:
    return [c.code for c in result.checks if level is None or c.level == level]


# --- entry price ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("side", "raw", "reference", "expected"),
    [
        # tick band edges: 0.1 below 100, 0.5 from 100, 1 from 500, 5 from 1000
        ("buy", "99.95", "100", "99.9"),
        ("sell", "99.95", "100", "100.0"),
        ("buy", "100.2", "100", "100.0"),
        ("sell", "100.2", "100", "100.5"),
        ("buy", "499.7", "480", "499.5"),
        ("sell", "499.7", "480", "500"),
        ("buy", "500.4", "500", "500"),
        ("sell", "500.4", "500", "501"),
        ("buy", "999.7", "950", "999"),
        ("sell", "999.2", "950", "1000"),
        ("buy", "1002", "1000", "1000"),
        ("sell", "1002", "1000", "1005"),
        ("buy", "9.995", "10", "9.99"),
        ("sell", "49.97", "50", "50.00"),
    ],
)
def test_entry_snaps_to_a_legal_tick(side, raw, reference, expected) -> None:
    result = run(
        plan(side, entry=raw),
        r=rules(reference, limited=False),
        a=account(held=5000),
    )
    assert result.entry_price == D(expected)
    assert is_on_tick(result.entry_price)
    if D(raw) != result.entry_price:
        assert "price_snapped" in codes(result, "info")


def test_etf_uses_the_etf_tick_bands() -> None:
    etf = rules("40", etf=True, limited=False)
    assert run(plan("buy", entry="39.987"), r=etf).entry_price == D("39.98")
    # at 50 and above an ETF ticks by 0.05, a stock by 0.1
    etf50 = rules("60", etf=True, limited=False)
    assert run(plan("buy", entry="60.07"), r=etf50).entry_price == D("60.05")
    assert run(plan("buy", entry="60.07"), r=rules("60", limited=False)).entry_price == D("60.0")


def test_limits_clamp_the_price() -> None:
    high = run(plan("buy", entry="115"))
    assert high.entry_price == D("110.0")
    assert "price_clamped" in codes(high, "adjusted")
    low = run(plan("sell", entry="80"), a=account(held=2000))
    assert low.entry_price == D("90.0")
    assert "price_clamped" in codes(low, "adjusted")
    # a snap that rounds a sell above the limit is clamped too
    near = run(plan("sell", entry="109.9"), r=rules("100"), a=account(held=2000))
    assert near.entry_price <= D("110")


def test_no_limit_symbol_is_not_clamped() -> None:
    result = run(plan("buy", entry="150"), r=rules("100", limited=False))
    assert result.entry_price == D("150")
    assert "price_clamped" not in codes(result)


def test_missing_entry_uses_the_reference_price() -> None:
    result = run(plan("buy", entry=None))
    assert result.entry_price == D("100")
    assert "entry_defaulted" in codes(result, "adjusted")
    sell = run(plan("sell", entry=None), a=account(held=3000))
    assert sell.entry_price == D("100")


# --- stop / target ----------------------------------------------------------------


def test_stop_and_target_are_snapped_and_kept_when_ordered() -> None:
    result = run(plan("buy", entry="100", stop="94.97", target="110.04"))
    assert result.stop_loss == D("94.9")
    assert result.take_profit == D("110.0")
    assert "stop_dropped" not in codes(result)


@pytest.mark.parametrize(
    ("stop", "target", "dropped"),
    [
        ("100", "110", ["stop_dropped"]),
        ("105", "110", ["stop_dropped"]),
        ("95", "100", ["target_dropped"]),
        ("95", "90", ["target_dropped"]),
        ("101", "99", ["stop_dropped", "target_dropped"]),
    ],
)
def test_offending_levels_are_dropped(stop, target, dropped) -> None:
    result = run(plan("buy", entry="100", stop=stop, target=target))
    assert sorted(codes(result, "adjusted")) == sorted(dropped)
    assert (result.stop_loss is None) == ("stop_dropped" in dropped)
    assert (result.take_profit is None) == ("target_dropped" in dropped)


def test_sell_drops_the_levels() -> None:
    result = run(plan("sell", entry="100", stop="90", target="120"), a=account(held=2000))
    assert result.stop_loss is None and result.take_profit is None
    assert "levels_dropped" in codes(result, "info")


# --- buy sizing -------------------------------------------------------------------


def test_buy_sizing_follows_the_hint_under_the_caps() -> None:
    # 20% of 1,000,000 = 200,000 -> at most 2 lots of a 100 stock
    normal = run(plan("buy", entry="100", stop="95", hint="normal"))
    assert normal.lots == 1 and normal.side == "buy" and normal.est_amount == 100_000
    full = run(plan("buy", entry="100", stop="95", hint="full"))
    assert full.lots == 2
    small = run(plan("buy", entry="100", stop="95", hint="small"))
    assert small.lots == 1  # a hint never rounds an affordable trade down to nothing
    assert "lots_capped" not in codes(full)


def test_size_hint_defaults_to_normal() -> None:
    assert (
        run(plan("buy", entry="20", stop="19", hint=None)).lots
        == run(plan("buy", entry="20", stop="19", hint="normal")).lots
    )


def test_risk_cap_reduces_the_lots() -> None:
    # stop 10% below: ~NT$10,540 per lot against a NT$20,000 budget -> 1 lot of the 2 wanted
    result = run(plan("buy", entry="100", stop="90", hint="full"))
    assert result.lots == 1
    assert "lots_capped" in codes(result, "adjusted")
    assert result.risk_amount is not None and result.risk_amount <= 20_000
    assert result.risk_pct == round(result.risk_amount / 1_000_000, 4)


def test_risk_includes_round_trip_costs() -> None:
    result = run(plan("buy", entry="100", stop="95", hint="normal"), a=account(discount="1.0"))
    # 5 x 1000 price drop + buy fee 142 + sell fee 135 + sell tax 285
    assert result.risk_amount == 5_000 + 142 + 135 + 285


def test_no_stop_means_no_risk_cap_and_a_note() -> None:
    result = run(plan("buy", entry="100", stop=None, hint="full"))
    assert result.lots == 2 and result.risk_amount is None and result.risk_pct is None
    assert "no_stop" in codes(result, "info")


def test_existing_position_counts_against_the_position_cap() -> None:
    held = run(plan("buy", entry="100", stop="95", hint="full"), a=account(held=1000))
    # 100,000 already held (1 lot at the mark): room for one more lot only
    assert held.lots == 1
    assert "lots_capped" in codes(held)
    assert held.position_pct == 0.2
    full = run(plan("buy", entry="100", stop="95", hint="full"), a=account(held=2000))
    assert full.lots is None and full.side is None
    assert "blocked_position_cap" in codes(full, "blocked")


def test_position_value_uses_the_last_price_when_known() -> None:
    # held 1 lot marked at 150: 150,000 held, 50,000 of room -> not even one 100 lot... it is
    result = run(
        plan("buy", entry="100", stop="95", hint="full"),
        r=rules("100", mark="150", limited=False),
        a=account(held=1000),
    )
    assert result.lots is None
    assert "blocked_position_cap" in codes(result, "blocked")


def test_cash_caps_the_lots_with_the_order_reserve() -> None:
    # one lot reserves 100,142 (price + fee); 250,000 cash covers two, a 20% cap of 5M covers 10
    result = run(
        plan("buy", entry="100", stop="95", hint="full"),
        a=account(equity=5_000_000, cash=250_000),
    )
    assert result.lots == 2
    assert "lots_capped" in codes(result)
    tight = run(
        plan("buy", entry="100", stop="95", hint="full"),
        a=account(equity=5_000_000, cash=200_000),
    )
    assert tight.lots == 1  # 2 x 100,142 > 200,000


def test_blocked_for_the_position_cap_explains_itself() -> None:
    result = run(
        plan("buy", entry="2180", stop="2090"),
        r=rules("2180"),
        a=account(equity=1_000_000, cash=640_000),
    )
    assert result.lots is None and result.side is None and not result.has_order
    blocked = [c for c in result.checks if c.level == "blocked"]
    assert [c.code for c in blocked] == ["blocked_position_cap"]
    assert blocked[0].message == (
        "資金不足一張：一張約需 NT$2,180,000，依單一部位上限 20% 可用 NT$200,000"
    )


def test_blocked_for_cash() -> None:
    result = run(
        plan("buy", entry="100", stop="95"),
        a=account(equity=5_000_000, cash=50_000),
    )
    assert codes(result, "blocked") == ["blocked_cash"]
    assert "可用資金不足一張" in result.checks[-1].message


def test_blocked_for_risk() -> None:
    # a 40% stop costs ~NT$40,500 a lot against a NT$20,000 risk budget
    result = run(plan("buy", entry="100", stop="60"))
    assert codes(result, "blocked") == ["blocked_risk_cap"]
    assert "風險超過上限" in result.checks[-1].message


def test_commission_discount_changes_the_reserve() -> None:
    # reserve for one lot with a 0.6 discount: 100,000 + 85; 2 lots need 200,171
    result = run(
        plan("buy", entry="100", stop="95", hint="full"),
        a=account(equity=5_000_000, cash=200_171, discount="0.6"),
    )
    assert result.lots == 2
    assert (
        run(
            plan("buy", entry="100", stop="95", hint="full"),
            a=account(equity=5_000_000, cash=200_170, discount="0.6"),
        ).lots
        == 1
    )


# --- sell sizing ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hint", "lots"),
    [("small", 1), ("normal", 2), ("full", 5), (None, 2)],
)
def test_sell_fractions(hint, lots) -> None:
    result = run(plan("sell", entry="100", hint=hint), a=account(held=5000), signal="reduce")
    assert result.lots == lots and result.side == "sell"
    assert result.est_amount == lots * 100_000


def test_sell_is_at_least_one_lot_when_any_is_sellable() -> None:
    assert run(plan("sell", entry="100", hint="small"), a=account(held=1000)).lots == 1


def test_sell_respects_shares_reserved_by_working_sells() -> None:
    held = account(held=5000, sellable=3000)  # 2 lots already offered for sale
    result = run(plan("sell", entry="100", hint="full"), a=held)
    assert result.lots == 3
    assert "lots_capped" in codes(result, "adjusted")


def test_sell_without_a_position_is_blocked() -> None:
    result = run(plan("sell", entry="100"), a=account(held=0))
    assert result.side is None
    assert [(c.code, c.message) for c in result.checks if c.level == "blocked"] == [
        ("blocked_no_position", "未持有此股票，無法賣出")
    ]


def test_sell_with_everything_reserved_is_blocked() -> None:
    result = run(plan("sell", entry="100"), a=account(held=2000, sellable=0))
    assert codes(result, "blocked") == ["blocked_not_sellable"]


# --- hold and alignment -----------------------------------------------------------


def test_hold_has_no_order_and_keeps_no_prices() -> None:
    result = run(plan("hold", entry="100", stop="90", target="120"), signal="avoid")
    assert not result.has_order and result.entry_price is None
    assert result.stop_loss is None and result.checks == [] and result.aligned


@pytest.mark.parametrize(
    ("action", "signal", "aligned"),
    [
        ("buy", "avoid", False),
        ("buy", "reduce", False),
        ("buy", "increase", True),
        ("buy", "hold", True),
        ("sell", "increase", False),
        ("sell", "reduce", True),
        ("sell", "avoid", True),
        ("sell", "hold", True),
        ("hold", "increase", True),
        ("hold", "avoid", True),
    ],
)
def test_alignment_with_the_quant_signal(action, signal, aligned) -> None:
    result = run(
        plan(action, entry="100", stop="95" if action == "buy" else None),
        a=account(held=3000 if action == "sell" else 0),
        signal=signal,
    )
    assert result.aligned is aligned
    assert ("signal_conflict" in codes(result, "info")) is (not aligned)
    # a conflict is information only: it never takes the order away
    if action != "hold":
        assert result.has_order


def test_position_pct_after_a_buy() -> None:
    result = run(plan("buy", entry="100", stop="95", hint="normal"))
    assert result.position_pct == 0.1  # 100,000 of 1,000,000
