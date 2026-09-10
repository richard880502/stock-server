from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


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
    branch_flows: Mapped[list[BrokerBranchFlowRow]] = relationship(
        back_populates="instrument"
    )


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

    instrument: Mapped[InstrumentRow] = relationship(
        back_populates="institutional_flows"
    )


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
