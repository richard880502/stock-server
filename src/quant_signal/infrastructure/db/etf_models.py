"""ORM row for ETF end-of-day quotes (kept out of ``market_stock_daily``)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from quant_signal.infrastructure.db.models import Base


class EtfDailyRow(Base):
    __tablename__ = "etf_daily"
    __table_args__ = (Index("ix_etf_daily_date", "trading_date"),)

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    trading_date: Mapped[date] = mapped_column(Date, primary_key=True)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    change: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    turnover_value: Mapped[float] = mapped_column(Float, nullable=False)
    trades: Mapped[int] = mapped_column(BigInteger, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
