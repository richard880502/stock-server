from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class MarketSymbolRow(Base):
    """Small integer ids for symbols, so minute rows store 2 bytes instead of a string."""

    __tablename__ = "market_symbols"
    id: Mapped[int] = mapped_column(SmallInteger, Identity(), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)


class MinuteBarRow(Base):
    """1-minute bars, partitioned by month (see migration 0013).

    Prices are integer hundredths (``price * 100``) and ``volume`` is the minute's own
    volume in the instrument's unit (lots for stocks, contracts for futures).
    ``official`` rows come from Shioaji's after-the-fact kbars and are never
    overwritten by live ticks; live rows may be partial (feed gaps, late start).
    Columns are ordered widest-first so rows pack without padding.
    """

    __tablename__ = "minute_bars"
    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[int] = mapped_column(Integer, nullable=False)
    high: Mapped[int] = mapped_column(Integer, nullable=False)
    low: Mapped[int] = mapped_column(Integer, nullable=False)
    close: Mapped[int] = mapped_column(Integer, nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    official: Mapped[bool] = mapped_column(Boolean, nullable=False)


class QuoteLatestRow(Base):
    """The newest known price per symbol today: one row each, overwritten in place.

    Filled by the collector from live ticks (subscribed symbols) and from
    whole-market snapshots every 10 minutes. ``reference`` is the exchange's
    reference price for the day (平盤價), which already accounts for ex-rights.
    """

    __tablename__ = "quote_latest"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    trade_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    amount: Mapped[int | None] = mapped_column(BigInteger)
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    reference: Mapped[int | None] = mapped_column(Integer)
    open: Mapped[int | None] = mapped_column(Integer)
    high: Mapped[int | None] = mapped_column(Integer)
    low: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), nullable=False)


class InstrumentRow(Base):
    __tablename__ = "instruments"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    market: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    bars: Mapped[list[DailyBarRow]] = relationship(back_populates="instrument")
    institutional_flows: Mapped[list[InstitutionalFlowRow]] = relationship(
        back_populates="instrument"
    )
    branch_flows: Mapped[list[BrokerBranchFlowRow]] = relationship(back_populates="instrument")


class DailyBarRow(Base):
    __tablename__ = "bars_daily"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "trading_date",
            "source",
            "revision",
            name="uq_bars_daily_version",
        ),
        Index("ix_bars_daily_instrument_date", "instrument_id", "trading_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    adjusted_close: Mapped[float | None] = mapped_column(Float)
    adjustment_factor: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    instrument: Mapped[InstrumentRow] = relationship(back_populates="bars")


class InstitutionalFlowRow(Base):
    __tablename__ = "institutional_flows_daily"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "trading_date",
            "source",
            "revision",
            name="uq_institutional_flow_version",
        ),
        Index(
            "ix_institutional_flow_instrument_date",
            "instrument_id",
            "trading_date",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    foreign_buy: Mapped[int] = mapped_column(BigInteger, nullable=False)
    foreign_sell: Mapped[int] = mapped_column(BigInteger, nullable=False)
    foreign_net: Mapped[int] = mapped_column(BigInteger, nullable=False)
    investment_trust_buy: Mapped[int] = mapped_column(BigInteger, nullable=False)
    investment_trust_sell: Mapped[int] = mapped_column(BigInteger, nullable=False)
    investment_trust_net: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dealer_buy: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dealer_sell: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dealer_net: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_net: Mapped[int] = mapped_column(BigInteger, nullable=False)
    foreign_holding_ratio: Mapped[float | None] = mapped_column(Float)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    instrument: Mapped[InstrumentRow] = relationship(back_populates="institutional_flows")


class BrokerBranchFlowRow(Base):
    __tablename__ = "broker_branch_flows_daily"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "trading_date",
            "branch_code",
            "source",
            "revision",
            name="uq_broker_branch_flow_version",
        ),
        Index(
            "ix_broker_branch_flow_instrument_date",
            "instrument_id",
            "trading_date",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    branch_code: Mapped[str] = mapped_column(String(32), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(255), nullable=False)
    buy_shares: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sell_shares: Mapped[int] = mapped_column(BigInteger, nullable=False)
    buy_amount: Mapped[float] = mapped_column(Float, nullable=False)
    sell_amount: Mapped[float] = mapped_column(Float, nullable=False)
    day_trade_buy_shares: Mapped[int | None] = mapped_column(BigInteger)
    day_trade_sell_shares: Mapped[int | None] = mapped_column(BigInteger)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    instrument: Mapped[InstrumentRow] = relationship(back_populates="branch_flows")


class SignalSnapshotRow(Base):
    __tablename__ = "signal_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "as_of",
            "strategy_version",
            name="uq_signal_snapshot",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    regime: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MarketObservationRow(Base):
    __tablename__ = "market_observations_daily"
    __table_args__ = (
        UniqueConstraint(
            "market",
            "trading_date",
            "source",
            "revision",
            name="uq_market_observation_version",
        ),
        Index("ix_market_observation_market_date", "market", "trading_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    index_close: Mapped[float] = mapped_column(Float, nullable=False)
    total_issues: Mapped[int] = mapped_column(Integer, nullable=False)
    advancing_issues: Mapped[int] = mapped_column(Integer, nullable=False)
    declining_issues: Mapped[int] = mapped_column(Integer, nullable=False)
    unchanged_issues: Mapped[int] = mapped_column(Integer, nullable=False)
    above_ma20_issues: Mapped[int] = mapped_column(Integer, nullable=False)
    above_ma60_issues: Mapped[int] = mapped_column(Integer, nullable=False)
    new_high_52w_issues: Mapped[int] = mapped_column(Integer, nullable=False)
    new_low_52w_issues: Mapped[int] = mapped_column(Integer, nullable=False)
    up_volume: Mapped[float] = mapped_column(Float, nullable=False)
    down_volume: Mapped[float] = mapped_column(Float, nullable=False)
    turnover_value: Mapped[float] = mapped_column(Float, nullable=False)
    foreign_net_flow: Mapped[float] = mapped_column(Float, nullable=False)
    investment_trust_net_flow: Mapped[float] = mapped_column(Float, nullable=False)
    dealer_net_flow: Mapped[float] = mapped_column(Float, nullable=False)
    futures_net_open_interest: Mapped[float] = mapped_column(Float, nullable=False)
    margin_balance: Mapped[float] = mapped_column(Float, nullable=False)
    short_balance: Mapped[float] = mapped_column(Float, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class MarketEnvironmentSnapshotRow(Base):
    __tablename__ = "market_environment_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "market",
            "as_of",
            "strategy_version",
            name="uq_market_environment_snapshot",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    regime: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChipFlowSnapshotRow(Base):
    __tablename__ = "chip_flow_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "as_of",
            "strategy_version",
            name="uq_chip_flow_snapshot",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BacktestJobRow(Base):
    __tablename__ = "backtest_jobs"
    __table_args__ = (Index("ix_backtest_jobs_status_created", "status", "created_at"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKeyRow(Base):
    __tablename__ = "api_keys"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NewsItemRow(Base):
    __tablename__ = "news_items"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "url",
            "source",
            "revision",
            name="uq_news_item_version",
        ),
        Index(
            "ix_news_item_instrument_published",
            "instrument_id",
            "published_at",
        ),
        Index("ix_news_item_instrument_available", "instrument_id", "available_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    sentiment_label: Mapped[str | None] = mapped_column(String(16))
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # When this system first held the item; as-of reads filter on it, not published_at.
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When the sentiment label was written (NULL: unclassified, or labelled at retrieval).
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class FetchCoverageRow(Base):
    __tablename__ = "fetch_coverage"

    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    period: Mapped[str] = mapped_column(String(16), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SyncJobRow(Base):
    __tablename__ = "sync_jobs"
    __table_args__ = (
        Index("ix_sync_jobs_status_created", "status", "created_at"),
        Index(
            "uq_sync_jobs_active",
            "kind",
            "symbol",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    lookback_days: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MarketStockDayRow(Base):
    __tablename__ = "market_stock_daily"
    __table_args__ = (Index("ix_market_stock_daily_date_market", "trading_date", "market"),)

    # Deliberately no foreign key to instruments: this table holds every listed
    # stock, while instruments is the (small) set the sync worker tracks.
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    turnover_value: Mapped[float] = mapped_column(Float, nullable=False)
    change: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    foreign_net: Mapped[int | None] = mapped_column(BigInteger)
    trust_net: Mapped[int | None] = mapped_column(BigInteger)
    dealer_net: Mapped[int | None] = mapped_column(BigInteger)
    total_net: Mapped[int | None] = mapped_column(BigInteger)
    margin_balance: Mapped[int | None] = mapped_column(BigInteger)
    margin_change: Mapped[int | None] = mapped_column(BigInteger)
    short_balance: Mapped[int | None] = mapped_column(BigInteger)
    short_change: Mapped[int | None] = mapped_column(BigInteger)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketIndustryRow(Base):
    __tablename__ = "market_industries"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    industry_code: Mapped[str] = mapped_column(String(8), nullable=False)
    industry_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("google_sub", name="uq_users_google_sub"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    google_sub: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    picture: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSessionRow(Base):
    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UsageCounterRow(Base):
    __tablename__ = "usage_counters"

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    action: Mapped[str] = mapped_column(String(32), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WatchlistItemRow(Base):
    __tablename__ = "watchlist_items"

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    # Name and market are copied at add time so a halted or renamed stock still lists.
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MarketEventRow(Base):
    """Official material announcements and ex-rights schedule (revisions kept)."""

    __tablename__ = "market_events"
    __table_args__ = (
        Index("ix_market_events_announced", "announced_at"),
        Index("ix_market_events_symbol_announced", "symbol", "announced_at"),
    )

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    external_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    announced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    clause: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)
    effective_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketRiskFlagRow(Base):
    """處置股 / 注意股 designations (revisions kept, e.g. an extended disposition)."""

    __tablename__ = "market_risk_flags"
    __table_args__ = (Index("ix_market_risk_flags_start", "start_date"),)

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    start_date: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    end_date: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MonthlyRevenueRow(Base):
    """Monthly revenue in NT$ thousands; a restated month is a new revision."""

    __tablename__ = "monthly_revenue"
    __table_args__ = (Index("ix_monthly_revenue_period", "period"),)

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    revenue: Mapped[float] = mapped_column(Float, nullable=False)
    mom_pct: Mapped[float | None] = mapped_column(Float)
    yoy_pct: Mapped[float | None] = mapped_column(Float)
    cumulative_revenue: Mapped[float | None] = mapped_column(Float)
    cumulative_yoy_pct: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketFactorDailyRow(Base):
    """Whole-market multi-factor snapshot, one row per stock, date and version."""

    __tablename__ = "market_factor_daily"
    __table_args__ = (Index("ix_market_factor_daily_date_version", "trading_date", "version"),)

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    version: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    industry_code: Mapped[str | None] = mapped_column(String(8))
    industry_name: Mapped[str | None] = mapped_column(String(64))
    close: Mapped[float] = mapped_column(Float, nullable=False)
    change_pct: Mapped[float | None] = mapped_column(Float)
    avg_turnover_20d: Mapped[float | None] = mapped_column(Float)
    composite: Mapped[float | None] = mapped_column(Float)
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    highlights: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    flow_through: Mapped[date | None] = mapped_column(Date)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ShareholdingDistributionRow(Base):
    """Weekly TDCC shareholding distribution; raw tiers kept as JSONB."""

    __tablename__ = "shareholding_distribution_weekly"
    __table_args__ = (Index("ix_shareholding_weekly_date", "data_date"),)

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    data_date: Mapped[date] = mapped_column(Date, primary_key=True)
    total_holders: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_shares: Mapped[int] = mapped_column(BigInteger, nullable=False)
    holders_ge400: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pct_ge400: Mapped[float] = mapped_column(Float, nullable=False)
    holders_ge1000: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pct_ge1000: Mapped[float] = mapped_column(Float, nullable=False)
    retail_holders: Mapped[int] = mapped_column(BigInteger, nullable=False)
    retail_pct: Mapped[float] = mapped_column(Float, nullable=False)
    tiers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ForeignHoldingRow(Base):
    """Daily shares outstanding and foreign holding from TWSE / TPEx."""

    __tablename__ = "foreign_holding_daily"
    __table_args__ = (Index("ix_foreign_holding_date", "trading_date"),)

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    shares_outstanding: Mapped[int] = mapped_column(BigInteger, nullable=False)
    foreign_shares: Mapped[int] = mapped_column(BigInteger, nullable=False)
    foreign_holding_pct: Mapped[float] = mapped_column(Float, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# Paper-trading tables live in their own module; importing it registers them on ``Base``
# (alembic and the schema-drift test only import this module).
from quant_signal.infrastructure.db import paper_models  # noqa: E402,F401
