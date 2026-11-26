"""Thin, quote-only adapter for Shioaji 1.3.3.

Callbacks run on SDK threads: consumers should enqueue ticks without blocking.
Stock volume is in exchange lots (1,000 shares); futures volume is in contracts.
History queries (kbars) are paced by the shared Redis budget in ``shioaji_budget``.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from quant_signal.infrastructure.providers.shioaji_budget import BudgetExhausted

_TAIPEI = ZoneInfo("Asia/Taipei")
_SYMBOL = re.compile(r"^[A-Z0-9]{1,16}(?:\.TW|\.TWO)?$")
# Stable symbol -> (futures root, continuous near-month code). TXFR1 always points
# at the current near-month TAIEX futures contract (its target_code, e.g. TXFL6).
_FUTURES = {"TXF.TAIFEX": ("TXF", "TXFR1")}


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def parse_kbars(symbol: str, kbars: Any, start: datetime, end: datetime) -> list[dict]:
    """Turn a Shioaji Kbars object into minute rows (start-of-minute, Asia/Taipei).

    Keeps minutes in ``[start, end)``; drops non-positive prices and malformed rows.
    Volume stays in the instrument's unit (lots for stocks, contracts for futures).
    """
    columns = [getattr(kbars, name, None) for name in ("ts", "Open", "High", "Low", "Close")]
    volumes = getattr(kbars, "Volume", None)
    if any(column is None for column in columns) or volumes is None:
        return []
    rows = []
    for ts, open_, high, low, close, volume in zip(*columns, volumes, strict=False):
        try:
            # ``ts`` is Taipei wall time encoded as if it were UTC (nanoseconds).
            wall = datetime.fromtimestamp(int(ts) / 1e9, UTC).replace(tzinfo=_TAIPEI)
            prices = [float(open_), float(high), float(low), float(close)]
            lots = int(volume)
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        if not all(math.isfinite(p) and p > 0 for p in prices) or lots < 0:
            continue
        if not (prices[2] <= min(prices[0], prices[3]) and prices[1] >= max(prices[0], prices[3])):
            continue
        # Bars are stamped at the END of their minute (stocks 09:01, futures 08:46).
        minute = wall - timedelta(minutes=1)
        if not start <= minute < end:
            continue
        rows.append(
            dict(
                symbol=symbol,
                minute=minute,
                open=prices[0],
                high=prices[1],
                low=prices[2],
                close=prices[3],
                first_time=minute,
                last_time=wall - timedelta(microseconds=1),
                first_total_volume=0,
                last_total_volume=lots,
                is_partial=False,
            )
        )
    return rows


class ShioajiFeed:
    def __init__(
        self,
        settings: Any,
        on_tick: Callable[[dict], None],
        on_disconnect: Callable[[], None],
        budget: Any = None,
    ) -> None:
        self._settings = settings
        self._on_tick = on_tick
        self._on_disconnect = on_disconnect
        # Without a budget no history query (kbars/ticks/snapshots) is ever sent.
        self._budget = budget
        self._api: Any = None
        self._sdk: Any = None
        # Subscription key (stock code or continuous futures code) -> (symbol, contract).
        self._subscriptions: dict[str, tuple[str, Any]] = {}
        # Tick code -> subscription key. Futures ticks carry the real contract code.
        self._tick_codes: dict[str, str] = {}
        self._closing = False

    def start(self) -> None:
        if self._api is not None:
            return
        try:
            import shioaji as sj
        except ImportError:
            raise RuntimeError("Shioaji SDK is not installed") from None
        api = None
        try:
            api = sj.Shioaji(simulation=self._settings.shioaji_simulation)
            api.quote.set_on_tick_stk_v1_callback(self._handle_tick)
            api.quote.set_on_tick_fop_v1_callback(self._handle_fop_tick)
            api.quote.set_event_callback(self._handle_event)
            api.login(
                api_key=self._settings.shioaji_api_key.get_secret_value(),
                secret_key=self._settings.shioaji_secret_key.get_secret_value(),
                subscribe_trade=False,
            )
        except Exception:
            if api is not None:
                try:
                    api.logout()
                except Exception:
                    pass
            raise RuntimeError("Shioaji login failed") from None
        self._closing = False
        self._sdk, self._api = sj, api

    def _resolve(self, symbol: str) -> tuple[str, Any, tuple[str, ...]]:
        """(subscription key, contract, tick codes) for a validated symbol."""
        symbol = symbol.strip().upper()
        if symbol in _FUTURES:
            root, code = _FUTURES[symbol]
            contract = getattr(getattr(self._api.Contracts.Futures, root, None), code, None)
            if contract is None or _value(getattr(contract, "exchange", None)) != "TAIFEX":
                raise ValueError("futures contract not found on TAIFEX")
            target = str(getattr(contract, "target_code", "") or "")
            return code, contract, tuple(c for c in (code, target) if c)
        if not _SYMBOL.fullmatch(symbol):
            raise ValueError("invalid stock symbol")
        code, _, suffix = symbol.partition(".")
        contract = self._api.Contracts.Stocks[code]
        exchange = _value(getattr(contract, "exchange", None))
        if (
            contract is None
            or exchange not in {"TSE", "OTC"}
            or (suffix == "TW" and exchange != "TSE")
            or (suffix == "TWO" and exchange != "OTC")
        ):
            raise ValueError("stock contract not found on TSE or OTC")
        return code, contract, (code,)

    def subscribe(self, symbol: str) -> None:
        if self._api is None:
            raise RuntimeError("Shioaji feed is not started")
        symbol = symbol.strip().upper()
        key, contract, codes = self._resolve(symbol)
        if key in self._subscriptions:
            if self._subscriptions[key][0] != symbol:
                raise ValueError("contract already subscribed under another symbol")
            return
        self._subscriptions[key] = (symbol, contract)
        for code in codes:
            self._tick_codes[code] = key
        try:
            self._api.quote.subscribe(
                contract,
                quote_type=self._sdk.constant.QuoteType.Tick,
                version=self._sdk.constant.QuoteVersion.v1,
            )
        except Exception:
            self._forget(key)
            raise RuntimeError("Shioaji subscription failed") from None

    def _forget(self, key: str) -> None:
        self._subscriptions.pop(key, None)
        for code in [code for code, owner in self._tick_codes.items() if owner == key]:
            self._tick_codes.pop(code, None)

    async def _query(self, kind: str, *args: Any, **kwargs: Any) -> Any:
        """The only path to Shioaji history queries: budgeted, then run off the loop."""
        if self._budget is None:
            raise BudgetExhausted("no Shioaji query budget configured")
        await self._budget.acquire(kind)
        api = self._api
        if api is None:
            raise RuntimeError("Shioaji feed is not started")
        try:
            return await asyncio.to_thread(getattr(api, kind), *args, **kwargs)
        except Exception:
            raise RuntimeError(f"Shioaji {kind} query failed") from None

    async def minute_bars(self, symbol: str, start: datetime, end: datetime) -> list[dict]:
        """Official 1-minute bars with ``start <= minute < end``, labelled by minute START.

        Shioaji's kbars are meant for after-the-fact use, not polling. Their ``ts`` is
        Taipei wall time encoded as if it were UTC (nanoseconds), and each bar is
        stamped at the END of its minute (the first stock bar is 09:01, the first
        TXFR1 bar 08:46). Volume is in lots (stocks) or contracts (futures). These
        readings come from the documented examples, not an explicit spec, so the
        caller compares them with live-built minutes.
        """
        if self._api is None:
            raise RuntimeError("Shioaji feed is not started")
        symbol = symbol.strip().upper()
        _, contract, _ = self._resolve(symbol)
        first = start.astimezone(_TAIPEI).date()
        last = (end.astimezone(_TAIPEI) - timedelta(microseconds=1)).date()
        if last < first:
            return []
        kbars = await self._query("kbars", contract, start=first.isoformat(), end=last.isoformat())
        return parse_kbars(symbol, kbars, start, end)

    def unsubscribe(self, symbol: str) -> None:
        symbol = symbol.strip().upper()
        key = _FUTURES[symbol][1] if symbol in _FUTURES else symbol.partition(".")[0]
        entry = self._subscriptions.get(key)
        if entry is None or entry[0] != symbol or self._api is None:
            return
        try:
            self._api.quote.unsubscribe(
                entry[1],
                quote_type=self._sdk.constant.QuoteType.Tick,
                version=self._sdk.constant.QuoteVersion.v1,
            )
        except Exception:
            raise RuntimeError("Shioaji unsubscription failed") from None
        self._forget(key)

    def close(self) -> None:
        self._closing = True
        api, self._api = self._api, None
        self._subscriptions.clear()
        self._tick_codes.clear()
        if api is not None:
            try:
                api.logout()
            except Exception:
                raise RuntimeError("Shioaji logout failed") from None

    def _handle_event(self, _response: int, event_code: int, _info: str, _event: str) -> None:
        if not self._closing and event_code in (1, 2):
            self._on_disconnect()

    def _handle_tick(self, exchange: Any, tick: Any) -> None:
        if self._closing or getattr(tick, "simtrade", True) or getattr(tick, "intraday_odd", True):
            return
        entry = self._subscriptions.get(self._tick_codes.get(str(tick.code), ""))
        if entry is None or entry[0] in _FUTURES or _value(exchange) != _value(entry[1].exchange):
            return
        self._emit(entry[0], tick, "lot")

    def _handle_fop_tick(self, exchange: Any, tick: Any) -> None:
        if self._closing or getattr(tick, "simtrade", True) or _value(exchange) != "TAIFEX":
            return
        entry = self._subscriptions.get(self._tick_codes.get(str(tick.code), ""))
        if entry is None or entry[0] not in _FUTURES:
            return
        self._emit(entry[0], tick, "contract")

    def _emit(self, symbol: str, tick: Any, unit: str) -> None:
        try:
            price = float(tick.close)
            change_pct = float(tick.pct_chg) / 100
            total_volume = int(tick.total_volume)
            # The trade's own size, so a minute's first trade is counted too.
            trade_volume = max(0, int(getattr(tick, "volume", 0) or 0))
            trade_time = tick.datetime
            if not isinstance(trade_time, datetime):
                return
            if trade_time.tzinfo is None:
                trade_time = trade_time.replace(tzinfo=_TAIPEI)
            else:
                trade_time = trade_time.astimezone(_TAIPEI)
            if (
                not math.isfinite(price)
                or price <= 0
                or not math.isfinite(change_pct)
                or total_volume < 0
            ):
                return
        except (AttributeError, TypeError, ValueError, OverflowError):
            return
        day_stats = {
            f"day_{name}": value
            for name in ("open", "high", "low")
            if (value := _positive_float(getattr(tick, name, None))) is not None
        }
        self._on_tick(
            {
                **day_stats,
                "symbol": symbol,
                "price": price,
                "change_pct": change_pct,
                "total_volume": total_volume,
                "volume": trade_volume,
                # "lot" = 張 (1,000 shares) for stocks, "contract" = 口 for futures.
                "unit": unit,
                "trade_time": trade_time.isoformat(),
                "received_at": datetime.now(UTC).isoformat(),
                "trade_epoch": trade_time.timestamp(),
            }
        )


def _positive_float(value: Any) -> float | None:
    """A finite, strictly positive float, else None (missing or bogus day stats)."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None
