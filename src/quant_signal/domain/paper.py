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
LedgerKind = Literal["deposit", "buy", "sell", "commission", "tax", "dividend", "reset"]

MAX_ACCOUNTS = 3
MIN_INITIAL_CASH = 100_000
MAX_INITIAL_CASH = 100_000_000
DEFAULT_INITIAL_CASH = 1_000_000


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


# --- requests ----------------------------------------------------------------------


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40)
    initial_cash: int = Field(
        default=DEFAULT_INITIAL_CASH, ge=MIN_INITIAL_CASH, le=MAX_INITIAL_CASH
    )
    commission_discount: Decimal = Field(
        default=Decimal("1.0"), ge=Decimal("0.1"), le=Decimal("1.0")
    )
    allow_day_trade: bool = False

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
    commission_discount: Decimal | None = Field(default=None, ge=Decimal("0.1"), le=Decimal("1.0"))
    allow_day_trade: bool | None = None

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("帳戶名稱不可空白")
        return value


class OrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=16)
    side: Side
    order_type: OrderType
    limit_price: Decimal | None = None
    lots: int
    note: str | None = Field(default=None, max_length=200)


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


class AccountList(BaseModel):
    accounts: list[AccountSummary]
    max_accounts: int


class OrderList(BaseModel):
    orders: list[Order]
    has_more: bool


class FillList(BaseModel):
    fills: list[Fill]
    has_more: bool


class EquityCurve(BaseModel):
    points: list[EquityPoint]
