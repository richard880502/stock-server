"""Deterministic layer behind AI trade proposals (pure, no I/O).

The model only suggests a direction, a price and a size *hint*. Everything that touches
money happens here: prices are snapped to legal ticks and the session's price limits,
quantities are computed in board lots and capped by the position limit, the risk limit
and the cash the account really has, and every intervention is reported as a ``Check``.
Nothing is ever placed from here; the output is only a proposal the user may accept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal
from typing import Literal

from quant_signal.domain.tw_trading import (
    BOARD_LOT,
    round_to_tick_down,
    round_to_tick_up,
)
from quant_signal.quant.paper_matching import compute_fill, reserve_for_buy, to_money

D = Decimal

Action = Literal["buy", "sell", "hold"]
SizeHint = Literal["small", "normal", "full"]
Level = Literal["info", "adjusted", "blocked"]

# Share of the per-position cap a "size_hint" asks for (buys) / of the held lots (sells).
BUY_HINT_FRACTION: dict[str, Decimal] = {"small": D("0.25"), "normal": D("0.5"), "full": D("1")}
SELL_HINT_FRACTION: dict[str, Decimal] = {
    "small": D(1) / D(3),
    "normal": D("0.5"),
    "full": D(1),
}
MAX_LOTS = 10_000

SIGNAL_LABELS = {
    "increase": "偏多配置",
    "hold": "維持觀察",
    "reduce": "降低風險",
    "avoid": "暫停進場",
}
# Signal actions that contradict a buy / a sell.
BUY_CONFLICTS = frozenset({"avoid", "reduce"})
SELL_CONFLICTS = frozenset({"increase"})


@dataclass(frozen=True)
class Check:
    code: str
    level: Level
    message: str

    def as_dict(self) -> dict:
        return {"code": self.code, "level": self.level, "message": self.message}


@dataclass(frozen=True)
class ModelPlan:
    """What the model asked for, already parsed (no lots: the model never sizes)."""

    action: Action
    entry_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    size_hint: SizeHint | None = None


@dataclass(frozen=True)
class MarketRules:
    reference: Decimal
    etf: bool
    limit_down: Decimal | None  # None: the symbol has no limit we can rely on
    limit_up: Decimal | None
    mark: Decimal | None = None  # last price, used to value the existing position

    @property
    def position_mark(self) -> Decimal:
        return self.mark if self.mark is not None else self.reference


@dataclass(frozen=True)
class AccountState:
    equity: int
    available_cash: int  # cash minus reserves of working buys (what an order may use)
    commission_discount: Decimal
    held_shares: int
    sellable_shares: int  # held minus working sells and (without day trading) today's buys
    symbol: str = ""


@dataclass(frozen=True)
class Limits:
    max_position_pct: Decimal
    max_risk_pct: Decimal


@dataclass
class Normalized:
    action: Action
    entry_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    side: Literal["buy", "sell"] | None = None
    lots: int | None = None
    est_amount: int | None = None
    checks: list[Check] = field(default_factory=list)
    risk_amount: int | None = None
    risk_pct: float | None = None
    position_pct: float | None = None
    aligned: bool = True

    @property
    def has_order(self) -> bool:
        return self.side is not None and self.lots is not None


def _money(value: Decimal | int) -> str:
    return f"NT${int(value):,}"


def _price(value: Decimal) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".") if value % 1 else f"{int(value):,}"


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator > 0 else None


def _largest(limit: int, fits) -> int:
    """Largest n in [0, limit] with ``fits(n)``, for a predicate that only turns False upward."""
    low, high = 0, max(limit, 0)
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    return low


def snap_entry(
    plan: ModelPlan, side: Literal["buy", "sell"], rules: MarketRules, checks: list[Check]
) -> Decimal:
    """A legal limit price: the model's, else the reference; snapped to a tick, kept in limits."""
    price = plan.entry_price
    if price is None or price <= 0:
        price = rules.reference
        checks.append(
            Check(
                "entry_defaulted",
                "adjusted",
                f"模型沒有給進出場價，改用參考價 {_price(rules.reference)}",
            )
        )
    snapped = (
        round_to_tick_down(price, etf=rules.etf)
        if side == "buy"
        else round_to_tick_up(price, etf=rules.etf)
    )
    if snapped != price:
        checks.append(
            Check(
                "price_snapped",
                "info",
                f"價格 {_price(price)} 不在升降單位上，已調整為 {_price(snapped)}",
            )
        )
    if rules.limit_up is not None and snapped > rules.limit_up:
        checks.append(
            Check(
                "price_clamped",
                "adjusted",
                f"價格 {_price(snapped)} 高於漲停價 {_price(rules.limit_up)}，已改為漲停價",
            )
        )
        snapped = rules.limit_up
    elif rules.limit_down is not None and snapped < rules.limit_down:
        checks.append(
            Check(
                "price_clamped",
                "adjusted",
                f"價格 {_price(snapped)} 低於跌停價 {_price(rules.limit_down)}，已改為跌停價",
            )
        )
        snapped = rules.limit_down
    return snapped


def _snap_level(value: Decimal | None, rules: MarketRules) -> Decimal | None:
    """Stop / target levels snap down (the cautious side for both a stop and a target)."""
    if value is None or value <= 0:
        return None
    return round_to_tick_down(value, etf=rules.etf)


def sanity_levels(
    plan: ModelPlan, entry: Decimal, rules: MarketRules, checks: list[Check]
) -> tuple[Decimal | None, Decimal | None]:
    """Stop and target of a buy: stop < entry < target, else the offending level is dropped."""
    stop, target = _snap_level(plan.stop_loss, rules), _snap_level(plan.take_profit, rules)
    if plan.stop_loss is not None and stop is None:
        checks.append(Check("stop_dropped", "adjusted", "停損價無效，已移除"))
    if plan.take_profit is not None and target is None:
        checks.append(Check("target_dropped", "adjusted", "停利價無效，已移除"))
    if stop is not None and stop >= entry:
        checks.append(
            Check(
                "stop_dropped",
                "adjusted",
                f"停損價 {_price(stop)} 不低於進場價 {_price(entry)}，已移除停損",
            )
        )
        stop = None
    if target is not None and target <= entry:
        checks.append(
            Check(
                "target_dropped",
                "adjusted",
                f"停利價 {_price(target)} 不高於進場價 {_price(entry)}，已移除停利",
            )
        )
        target = None
    return stop, target


def round_trip_risk(
    shares: int, entry: Decimal, stop: Decimal, *, symbol: str, discount: Decimal
) -> int:
    """Loss if the stop is hit: the price drop plus buy and sell costs (whole NT$)."""
    buy = compute_fill("buy", shares, entry, symbol=symbol, commission_discount=discount)
    sell = compute_fill("sell", shares, stop, symbol=symbol, commission_discount=discount)
    return to_money((entry - stop) * shares) + buy.commission + sell.commission + sell.tax


def size_buy(
    entry: Decimal,
    stop: Decimal | None,
    hint: SizeHint | None,
    account: AccountState,
    rules: MarketRules,
    limits: Limits,
    checks: list[Check],
) -> int:
    """Lots for a buy, 0 (with a ``blocked`` check) when not even one board lot fits."""
    lot_cost = to_money(entry * BOARD_LOT)
    reserve_one = reserve_for_buy(BOARD_LOT, entry, account.commission_discount)
    existing_value = to_money(D(account.held_shares) * rules.position_mark)
    cap_amount = to_money(D(account.equity) * limits.max_position_pct)
    room = max(cap_amount - existing_value, 0)
    pct_text = f"{(limits.max_position_pct * 100).normalize():f}%"

    by_position = min(room // lot_cost if lot_cost else 0, MAX_LOTS)
    by_cash = _largest(
        MAX_LOTS,
        lambda n: (
            reserve_for_buy(n * BOARD_LOT, entry, account.commission_discount)
            <= account.available_cash
        ),
    )
    if stop is not None:
        budget = to_money(D(account.equity) * limits.max_risk_pct)
        by_risk = _largest(
            MAX_LOTS,
            lambda n: (
                round_trip_risk(
                    n * BOARD_LOT,
                    entry,
                    stop,
                    symbol=account.symbol,
                    discount=account.commission_discount,
                )
                <= budget
            ),
        )
    else:
        budget, by_risk = 0, MAX_LOTS
        checks.append(Check("no_stop", "info", "沒有可用的停損價，單筆風險上限未套用"))

    hard = min(by_position, by_cash, by_risk)
    if hard < 1:
        existing = f"（已持有市值 {_money(existing_value)}）" if existing_value else ""
        position_message = (
            f"資金不足一張：一張約需 {_money(lot_cost)}，"
            f"依單一部位上限 {pct_text} 可用 {_money(room)}{existing}"
        )
        # Name the binding rule: the tighter of the position cap and the cash, then risk.
        position_binds = by_position < 1 and (by_cash >= 1 or room <= account.available_cash)
        if position_binds:
            message = position_message
            code = "blocked_position_cap"
        elif by_cash < 1:
            message = (
                f"可用資金不足一張：一張約需預留 {_money(reserve_one)}，"
                f"目前可用 {_money(account.available_cash)}"
            )
            code = "blocked_cash"
        else:
            assert stop is not None
            one_risk = round_trip_risk(
                BOARD_LOT,
                entry,
                stop,
                symbol=account.symbol,
                discount=account.commission_discount,
            )
            message = (
                f"風險超過上限：一張停損風險約 {_money(one_risk)}，"
                f"單筆風險上限 {(limits.max_risk_pct * 100).normalize():f}%"
                f"（{_money(budget)}）"
            )
            code = "blocked_risk_cap"
        checks.append(Check(code, "blocked", message))
        return 0

    fraction = BUY_HINT_FRACTION.get(hint or "normal", BUY_HINT_FRACTION["normal"])
    wanted = int((D(cap_amount) * fraction / D(lot_cost)).to_integral_value(ROUND_FLOOR))
    wanted = max(wanted, 1)  # a hint never turns an affordable trade into "zero lots"
    lots = min(wanted, hard)
    if lots < wanted:
        reasons = []
        if by_cash == hard:
            reasons.append("可用資金")
        if by_position == hard:
            reasons.append(f"單一部位上限 {pct_text}")
        if by_risk == hard:
            reasons.append(f"單筆風險上限 {(limits.max_risk_pct * 100).normalize():f}%")
        checks.append(
            Check(
                "lots_capped",
                "adjusted",
                f"依建議規模為 {wanted} 張，受{'、'.join(reasons)}限制，調整為 {lots} 張",
            )
        )
    return lots


def size_sell(hint: SizeHint | None, account: AccountState, checks: list[Check]) -> int:
    """Lots for a sell out of the sellable shares; 0 with a ``blocked`` check when none."""
    held_lots = account.held_shares // BOARD_LOT
    sellable_lots = account.sellable_shares // BOARD_LOT
    if held_lots < 1:
        checks.append(Check("blocked_no_position", "blocked", "未持有此股票，無法賣出"))
        return 0
    if sellable_lots < 1:
        checks.append(
            Check(
                "blocked_not_sellable",
                "blocked",
                f"持有 {account.held_shares:,} 股，但目前沒有可賣股數"
                "（已有賣單預留，或當日買進不可賣出）",
            )
        )
        return 0
    fraction = SELL_HINT_FRACTION.get(hint or "normal", SELL_HINT_FRACTION["normal"])
    wanted = max(int((D(held_lots) * fraction).to_integral_value(ROUND_FLOOR)), 1)
    lots = min(wanted, sellable_lots)
    if lots < wanted:
        checks.append(
            Check(
                "lots_capped",
                "adjusted",
                f"依建議規模為 {wanted} 張，但可賣只有 {sellable_lots} 張，調整為 {lots} 張",
            )
        )
    return lots


def check_alignment(action: Action, signal_action: str | None, checks: list[Check]) -> bool:
    """False (with an info check) when the proposal runs against the quant signal. Never blocks."""
    if signal_action is None or action == "hold":
        return True
    conflict = (action == "buy" and signal_action in BUY_CONFLICTS) or (
        action == "sell" and signal_action in SELL_CONFLICTS
    )
    if not conflict:
        return True
    label = SIGNAL_LABELS.get(signal_action, signal_action)
    verb = "買進" if action == "buy" else "賣出"
    checks.append(
        Check(
            "signal_conflict",
            "info",
            f"AI 提案{verb}，但量化訊號為「{label}」，兩者方向不一致，請自行判斷",
        )
    )
    return False


def normalize(
    plan: ModelPlan,
    *,
    rules: MarketRules,
    account: AccountState,
    limits: Limits,
    signal_action: str | None,
) -> Normalized:
    """Turn the model's plan into a checked proposal (an order, or none)."""
    out = Normalized(action=plan.action)
    out.aligned = check_alignment(plan.action, signal_action, out.checks)
    if plan.action == "hold":
        return out
    side: Literal["buy", "sell"] = "buy" if plan.action == "buy" else "sell"
    entry = snap_entry(plan, side, rules, out.checks)
    out.entry_price = entry
    if side == "buy":
        out.stop_loss, out.take_profit = sanity_levels(plan, entry, rules, out.checks)
        lots = size_buy(entry, out.stop_loss, plan.size_hint, account, rules, limits, out.checks)
    else:
        if plan.stop_loss is not None or plan.take_profit is not None:
            out.checks.append(Check("levels_dropped", "info", "賣出提案不使用停損／停利價，已忽略"))
        lots = size_sell(plan.size_hint, account, out.checks)
    if lots < 1:
        return out
    shares = lots * BOARD_LOT
    amount = to_money(D(shares) * entry)
    out.side, out.lots, out.est_amount = side, lots, amount
    if side == "buy":
        existing = to_money(D(account.held_shares) * rules.position_mark)
        out.position_pct = _ratio(existing + amount, account.equity)
        if out.stop_loss is not None:
            out.risk_amount = round_trip_risk(
                shares,
                entry,
                out.stop_loss,
                symbol=account.symbol,
                discount=account.commission_discount,
            )
            out.risk_pct = _ratio(out.risk_amount, account.equity)
    else:
        held_value = to_money(D(account.held_shares) * rules.position_mark)
        out.position_pct = _ratio(max(held_value - amount, 0), account.equity)
    return out
