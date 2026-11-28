"""Paper-trading (模擬交易) models: stored records and the REST contract.

Money is integer NT$; prices are floats with at most two decimals on the wire and
``Decimal`` inside. Times are timezone-aware.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]
OrderStatus = Literal["working", "filled", "cancelled", "expired", "rejected"]
OrderSource = Literal["manual", "ai"]
PriceSource = Literal["tick", "snapshot", "minute_bar", "open"]
LedgerKind = Literal[
    "deposit",  # the opening deposit of a new account
    "buy",
    "sell",
    "commission",
    "tax",
    "dividend",
    "reset",  # the re-deposit of ``initial_cash`` that starts a new epoch
    "transfer_in",  # a later deposit by the user (not P&L)
    "transfer_out",  # a withdrawal by the user (not P&L)
]
TRANSFER_KINDS: tuple[str, str] = ("transfer_in", "transfer_out")
TransferDirection = Literal["in", "out"]

MAX_ACCOUNTS = 3
MIN_INITIAL_CASH = 100_000
MAX_INITIAL_CASH = 100_000_000
DEFAULT_INITIAL_CASH = 1_000_000
MAX_LOTS_PER_ORDER = 10_000
MIN_TRANSFER = 1_000
MAX_TRANSFER = 100_000_000
MAX_CASH = 1_000_000_000  # a deposit may not take the cash balance above this
MAX_TRANSFERS_PER_DAY = 20  # per account and Taipei calendar day
MAX_TRANSFER_NOTE = 60
MAX_LIMIT_PRICE = Decimal("100000")  # exclusive; the column holds 10 digits before the point
MIN_DISCOUNT = Decimal("0.1")
MAX_DISCOUNT = Decimal("1.0")


def _check_discount(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if not value.is_finite() or not (MIN_DISCOUNT <= value <= MAX_DISCOUNT):
        raise ValueError("手續費折數必須介於 0.1 到 1.0 之間")
    if value != value.quantize(Decimal("0.0001")):
        raise ValueError("手續費折數最多 4 位小數")
    return value


# --- stored records ----------------------------------------------------------------


class PaperAccountRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    name: str
    initial_cash: int
    commission_discount: Decimal = Decimal("1.0")
    allow_day_trade: bool = False
    created_at: datetime
    reset_at: datetime | None = None
    archived: bool = False
    epoch: int = 0


class PaperOrderRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    account_id: UUID
    epoch: int = 0
    symbol: str
    name: str
    side: Side
    order_type: OrderType
    limit_price: Decimal | None = None
    lots: int
    status: OrderStatus = "working"
    source: OrderSource = "manual"
    session_date: date
    created_at: datetime
    updated_at: datetime
    reject_reason: str | None = None
    reserved_cash: int = 0
    # shares x cap price the reserve was sized from (buys only): it lets a change of the
    # commission discount recompute ``reserved_cash`` exactly.
    reserve_amount: int = 0
    note: str | None = None


class PaperFillRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    order_id: UUID
    account_id: UUID
    epoch: int = 0
    symbol: str
    side: Side
    shares: int
    price: Decimal
    amount: int
    commission: int
    tax: int
    day_trade: bool = False
    filled_at: datetime
    price_source: PriceSource
    price_time: datetime


class LedgerEntry(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    account_id: UUID
    epoch: int = 0
    at: datetime
    kind: LedgerKind
    amount: int
    fill_id: UUID | None = None
    # Set on dividend postings: the corporate action (paper_corporate_actions) they came from.
    action_id: UUID | None = None
    # Set on transfer postings (transfer_in / transfer_out): the client's idempotency key,
    # unique per account.
    request_id: UUID | None = None
    note: str | None = None


class SnapshotRecord(BaseModel):
    account_id: UUID
    trading_date: date
    epoch: int = 0
    cash: int
    market_value: int
    equity: int
    realized_pnl_cum: int
    benchmark_close: float | None = None
    # Net deposits minus withdrawals dated in (previous trading day, trading_date]: the
    # flow that has to be removed from this day's equity change (time-weighted return).
    net_transfer: int = 0


# --- requests ----------------------------------------------------------------------


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40)
    initial_cash: int = Field(default=DEFAULT_INITIAL_CASH)
    commission_discount: Decimal = Decimal("1.0")
    allow_day_trade: bool = False

    @field_validator("initial_cash")
    @classmethod
    def _cash(cls, value: int) -> int:
        if not (MIN_INITIAL_CASH <= value <= MAX_INITIAL_CASH):
            raise ValueError(
                f"初始資金必須介於 NT${MIN_INITIAL_CASH:,} 到 NT${MAX_INITIAL_CASH:,} 之間"
            )
        return value

    @field_validator("commission_discount")
    @classmethod
    def _discount(cls, value: Decimal) -> Decimal:
        checked = _check_discount(value)
        assert checked is not None
        return checked

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("帳戶名稱不可空白")
        return value


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=40)
    commission_discount: Decimal | None = None
    allow_day_trade: bool | None = None

    @field_validator("commission_discount")
    @classmethod
    def _discount(cls, value: Decimal | None) -> Decimal | None:
        return _check_discount(value)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("帳戶名稱不可空白")
        return value


class ResetRequest(BaseModel):
    """Optional body of ``POST /accounts/{id}/reset``: start the new epoch with this cash."""

    model_config = ConfigDict(extra="forbid")

    initial_cash: int | None = None

    @field_validator("initial_cash")
    @classmethod
    def _cash(cls, value: int | None) -> int | None:
        if value is not None and not (MIN_INITIAL_CASH <= value <= MAX_INITIAL_CASH):
            raise ValueError(
                f"初始資金必須介於 NT${MIN_INITIAL_CASH:,} 到 NT${MAX_INITIAL_CASH:,} 之間"
            )
        return value


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: TransferDirection
    amount: int
    note: str | None = Field(default=None, max_length=MAX_TRANSFER_NOTE)
    request_id: UUID

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: int) -> int:
        if not (MIN_TRANSFER <= value <= MAX_TRANSFER):
            raise ValueError(f"金額必須介於 NT${MIN_TRANSFER:,} 到 NT${MAX_TRANSFER:,} 之間")
        return value

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class OrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=16)
    side: Side
    order_type: OrderType
    limit_price: Decimal | None = None
    lots: int
    note: str | None = Field(default=None, max_length=200)
    # Accepting an AI proposal (see application/paper_proposals.py): the order is placed
    # with source="ai" and the proposal is marked accepted in the same transaction.
    proposal_id: UUID | None = None

    @field_validator("lots")
    @classmethod
    def _lots(cls, value: int) -> int:
        if not (1 <= value <= MAX_LOTS_PER_ORDER):
            raise ValueError(f"張數必須介於 1 到 {MAX_LOTS_PER_ORDER:,} 張之間")
        return value

    @field_validator("limit_price")
    @classmethod
    def _limit_price(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if not value.is_finite() or value <= 0:
            raise ValueError("限價必須大於 0")
        if value >= MAX_LIMIT_PRICE:
            raise ValueError(f"限價必須小於 {MAX_LIMIT_PRICE:,.0f}")
        if value != value.quantize(Decimal("0.01")):
            raise ValueError("限價最多 2 位小數")
        return value


# --- responses ---------------------------------------------------------------------


class Fill(BaseModel):
    id: UUID
    order_id: UUID
    symbol: str
    side: Side
    shares: int
    price: float
    amount: int
    commission: int
    tax: int
    day_trade: bool
    filled_at: datetime
    price_source: PriceSource
    price_time: datetime
    realized_pnl: int | None = None


class Order(BaseModel):
    id: UUID
    symbol: str
    name: str
    side: Side
    order_type: OrderType
    limit_price: float | None
    lots: int
    status: OrderStatus
    source: OrderSource
    session_date: date
    created_at: datetime
    updated_at: datetime
    reject_reason: str | None
    reserved_cash: int
    note: str | None
    fill: Fill | None = None


class Position(BaseModel):
    symbol: str
    name: str
    market: Literal["TW", "TWO"]
    shares: int
    lots: int
    available_shares: int
    avg_cost: float
    cost_basis: int
    price: float
    price_time: datetime | None
    price_delayed: bool
    market_value: int
    unrealized_pnl: int
    unrealized_pct: float | None
    day_change_pct: float | None
    weight_pct: float
    # Cash dividends received while holding this position (gross, NT$). Not part of
    # ``unrealized_pnl`` / ``unrealized_pct``, which are price-only.
    dividend_income: int = 0


class AccountSummary(BaseModel):
    id: UUID
    name: str
    initial_cash: int
    cash: int
    available_cash: int
    market_value: int
    equity: int
    unrealized_pnl: int
    realized_pnl: int
    # Cash dividends credited since the last reset (gross, NT$). ``realized_pnl`` is trading
    # P&L only; total_pnl = realized_pnl + unrealized_pnl + dividend_income.
    dividend_income: int = 0
    # Initial cash + deposits - withdrawals of the current epoch; total_pnl = equity - this.
    # Returns are time-weighted (transfers are neither profit nor loss).
    contributed_capital: int
    # Net transfers counted in today's figures (those after the previous snapshot day);
    # day_pnl = equity - previous close - day_net_transfer.
    day_net_transfer: int = 0
    total_pnl: int
    total_return_pct: float
    day_pnl: int
    day_return_pct: float
    positions_count: int
    working_orders: int
    commission_discount: float
    allow_day_trade: bool
    created_at: datetime
    prices_as_of: datetime | None


class Benchmark(BaseModel):
    name: str
    return_pct: float
    since: date


class AccountDetail(AccountSummary):
    positions: list[Position]
    benchmark: Benchmark | None


class OrderPreview(BaseModel):
    ok: bool
    reject_reason: str | None
    symbol: str
    name: str
    side: Side
    order_type: OrderType
    lots: int
    shares: int
    reference_price: float | None
    last_price: float | None
    last_price_time: datetime | None
    price_delayed: bool
    # None when the symbol has no daily price limit we can rely on (most ETFs).
    limit_up: float | None
    limit_down: float | None
    tick: float | None
    estimated_price: float | None
    estimated_amount: int | None
    estimated_commission: int | None
    estimated_tax: int | None
    estimated_total: int | None
    reserve: int
    available_cash_after: int | None
    session_date: date
    will_queue_for_next_session: bool
    warnings: list[str]


class EquityPoint(BaseModel):
    date: date
    equity: int
    cash: int
    market_value: int
    benchmark_close: float | None
    live: bool = False
    # Net deposits - withdrawals counted on this point (see SnapshotRecord.net_transfer).
    net_transfer: int = 0
    # Time-weighted return index, 100 at the first point of the series.
    twr_index: float | None = None


class AccountList(BaseModel):
    accounts: list[AccountSummary]
    max_accounts: int


class OrderList(BaseModel):
    orders: list[Order]
    has_more: bool


class FillList(BaseModel):
    fills: list[Fill]
    has_more: bool


class Transfer(BaseModel):
    id: UUID
    direction: TransferDirection
    amount: int  # always positive; ``direction`` says which way
    at: datetime
    note: str | None = None


class TransferList(BaseModel):
    transfers: list[Transfer]
    has_more: bool


class EquityCurve(BaseModel):
    points: list[EquityPoint]
