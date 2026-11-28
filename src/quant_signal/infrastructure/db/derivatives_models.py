"""ORM rows for the TAIFEX derivatives datasets (see ``domain/derivatives.py`` for units)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from quant_signal.infrastructure.db.models import Base


class _Provenance:
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FuturesInstitutionalRow(_Provenance, Base):
    __tablename__ = "futures_institutional_daily"

    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    contract: Mapped[str] = mapped_column(String(8), primary_key=True)
    investor: Mapped[str] = mapped_column(String(8), primary_key=True)
    long_trade_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    long_trade_value_ntd_k: Mapped[int] = mapped_column(BigInteger, nullable=False)
    short_trade_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    short_trade_value_ntd_k: Mapped[int] = mapped_column(BigInteger, nullable=False)
    long_oi_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    long_oi_value_ntd_k: Mapped[int] = mapped_column(BigInteger, nullable=False)
    short_oi_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    short_oi_value_ntd_k: Mapped[int] = mapped_column(BigInteger, nullable=False)


class OptionsPcRatioRow(_Provenance, Base):
    __tablename__ = "options_pc_ratio_daily"

    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    put_volume_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    call_volume_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    volume_ratio_pct: Mapped[float] = mapped_column(Float, nullable=False)
    put_oi_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    call_oi_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    oi_ratio_pct: Mapped[float] = mapped_column(Float, nullable=False)


class OptionsInstitutionalRow(_Provenance, Base):
    __tablename__ = "options_institutional_daily"

    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    contract: Mapped[str] = mapped_column(String(8), primary_key=True)
    call_put: Mapped[str] = mapped_column(String(4), primary_key=True)
    investor: Mapped[str] = mapped_column(String(8), primary_key=True)
    buy_trade_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    buy_trade_value_ntd_k: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sell_trade_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sell_trade_value_ntd_k: Mapped[int] = mapped_column(BigInteger, nullable=False)
    buy_oi_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    buy_oi_value_ntd_k: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sell_oi_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sell_oi_value_ntd_k: Mapped[int] = mapped_column(BigInteger, nullable=False)


class FuturesLargeTradersRow(_Provenance, Base):
    __tablename__ = "futures_large_traders_daily"

    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    contract: Mapped[str] = mapped_column(String(8), primary_key=True)
    trader_type: Mapped[str] = mapped_column(String(8), primary_key=True)
    top5_long_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    top5_short_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    top10_long_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    top10_short_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    market_oi_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)


class StockFuturesRow(_Provenance, Base):
    __tablename__ = "stock_futures_daily"
    __table_args__ = (Index("ix_stock_futures_date", "trading_date"),)

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    underlying_code: Mapped[str] = mapped_column(String(16), nullable=False)
    total_open_interest: Mapped[float | None] = mapped_column(Float)
    total_volume: Mapped[float] = mapped_column(Float, nullable=False)
    open_interest_shares: Mapped[int | None] = mapped_column(BigInteger)
    volume_shares: Mapped[int] = mapped_column(BigInteger, nullable=False)
    near_month: Mapped[str | None] = mapped_column(String(8))
    near_settlement: Mapped[float | None] = mapped_column(Float)
    near_volume: Mapped[float | None] = mapped_column(Float)
    near_open_interest: Mapped[float | None] = mapped_column(Float)
    product_count: Mapped[int] = mapped_column(BigInteger, nullable=False)


class OptionsOiLevelRow(_Provenance, Base):
    __tablename__ = "options_oi_levels_daily"

    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    expiry: Mapped[str] = mapped_column(String(16), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    max_call_strike: Mapped[float | None] = mapped_column(Float)
    max_call_oi_contracts: Mapped[int | None] = mapped_column(BigInteger)
    max_put_strike: Mapped[float | None] = mapped_column(Float)
    max_put_oi_contracts: Mapped[int | None] = mapped_column(BigInteger)
    call_total_oi_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    put_total_oi_contracts: Mapped[int] = mapped_column(BigInteger, nullable=False)


class VixRow(_Provenance, Base):
    __tablename__ = "vix_daily"

    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    vix_close: Mapped[float] = mapped_column(Float, nullable=False)
    vix_last_minute_avg: Mapped[float | None] = mapped_column(Float)
