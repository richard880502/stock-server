"""ETF daily quotes (``etf_daily``), kept apart from the ordinary-stock market table.

Whole-market statistics (screener, rankings, sectors, breadth) read ``market_stock_daily``
and must stay stock-only, so ETFs live in their own table and are read only by paper
trading, search and the quick stock page.

Inclusion rule (verified against the TWSE MI_INDEX and TPEx dailyQuotes payloads of
2026-11-27): an ETF is a security whose code starts with ``00`` and matches
``00\\d{2,4}[A-Z]?`` - 0050, 00878, 006208, 00631L (leveraged), 00632R (inverse),
00679B (bond), 00400A (active), 00980D / 00687C / 00980T (other share classes and
currencies). Excluded: ETNs (``02xxxx``, including ``02001L``), 受益證券 (``01xxxT``),
special shares and depositary receipts (``1101B``, ``910322``) and warrants.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ETF_CODE = re.compile(r"^00\d{2,4}[A-Z]?$")


def is_etf_code(code: str) -> bool:
    """Whether a bare exchange code (no ``.TW`` suffix) is an ETF we store."""
    return ETF_CODE.fullmatch(code.strip().upper()) is not None


def is_etf_symbol(symbol: str) -> bool:
    """``0050.TW`` / ``00679B.TWO`` / ``0050``: same rule as ``is_etf_code``."""
    return is_etf_code(symbol.strip().upper().split(".")[0])


class EtfDay(BaseModel):
    """One ETF's official end-of-day quote. Volume in shares, turnover in NT$."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    name: str
    market: Literal["TW", "TWO"]
    trading_date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    change: float = 0.0
    volume: int = Field(ge=0)
    turnover_value: float = Field(ge=0)
    trades: int = Field(ge=0)
    available_at: datetime
    source: str
