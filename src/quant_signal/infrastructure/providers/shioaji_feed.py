"""Thin, quote-only adapter for Shioaji 1.3.3.

Callbacks run on SDK threads: consumers should enqueue ticks without blocking.
Total volume is in exchange lots (1,000 shares), not shares.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

_TAIPEI = ZoneInfo("Asia/Taipei")
_SYMBOL = re.compile(r"^[A-Z0-9]{1,16}(?:\.TW|\.TWO)?$")


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def parse_kbars(symbol: str, kbars: Any, day: date) -> list[dict]:
    """Turn a Shioaji Kbars object into minute rows (start-of-minute, Asia/Taipei).

    Rows outside ``day`` or with non-positive prices are dropped; volume stays in lots.
    """
    columns = [getattr(kbars, name, None) for name in ("ts", "Open", "High", "Low", "Close")]
    volumes = getattr(kbars, "Volume", None)
    if any(column is None for column in columns) or volumes is None:
        return []
    rows = []
    for ts, open_, high, low, close, volume in zip(*columns, volumes, strict=False):
        try:
            wall = datetime.fromtimestamp(int(ts) / 1e9, UTC).replace(tzinfo=_TAIPEI)
            prices = [float(open_), float(high), float(low), float(close)]
            lots = int(volume)
        except (TypeError, ValueError, OverflowError):
            continue
        if not all(math.isfinite(p) and p > 0 for p in prices) or lots < 0:
            continue
        minute = wall - timedelta(minutes=1)
        if minute.date() != day:
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
        self, settings: Any, on_tick: Callable[[dict], None], on_disconnect: Callable[[], None]
    ) -> None:
        self._settings = settings
        self._on_tick = on_tick
        self._on_disconnect = on_disconnect
        self._api: Any = None
        self._sdk: Any = None
        # SDK tick codes have no market suffix. Keep the exact subscribed symbol.
        self._subscriptions: dict[str, tuple[str, Any]] = {}
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

    def subscribe(self, symbol: str) -> None:
        if self._api is None:
            raise RuntimeError("Shioaji feed is not started")
        symbol = symbol.strip().upper()
        if not _SYMBOL.fullmatch(symbol):
            raise ValueError("invalid stock symbol")
        code, _, suffix = symbol.partition(".")
        if code in self._subscriptions:
            if self._subscriptions[code][0] != symbol:
                raise ValueError("stock code already subscribed under another symbol")
            return
        contract = self._api.Contracts.Stocks[code]
        exchange = _value(getattr(contract, "exchange", None))
        if (
            contract is None
            or exchange not in {"TSE", "OTC"}
            or (suffix == "TW" and exchange != "TSE")
            or (suffix == "TWO" and exchange != "OTC")
        ):
            raise ValueError("stock contract not found on TSE or OTC")
        self._subscriptions[code] = (symbol, contract)
        try:
            self._api.quote.subscribe(
                contract,
                quote_type=self._sdk.constant.QuoteType.Tick,
                version=self._sdk.constant.QuoteVersion.v1,
            )
        except Exception:
            self._subscriptions.pop(code, None)
            raise RuntimeError("Shioaji subscription failed") from None

    def minute_bars(self, symbol: str, day: date) -> list[dict]:
        """Official 1-minute bars for one trading day, labelled by minute START in Taipei.

        Shioaji's kbars are meant for after-the-fact use, not polling. Their ``ts`` is
        Taipei wall time encoded as if it were UTC (nanoseconds), and each bar is
        stamped at the END of its minute (the first bar of a session is 09:01).
        Volume is in lots. These readings come from the documented examples, not an
        explicit spec, so the caller compares them with live-built minutes.
        """
        if self._api is None:
            raise RuntimeError("Shioaji feed is not started")
        symbol = symbol.strip().upper()
        if not _SYMBOL.fullmatch(symbol):
            raise ValueError("invalid stock symbol")
        contract = self._api.Contracts.Stocks[symbol.partition(".")[0]]
        if contract is None:
            raise ValueError("stock contract not found")
        try:
            kbars = self._api.kbars(contract, start=day.isoformat(), end=day.isoformat())
        except Exception:
            raise RuntimeError("Shioaji kbars query failed") from None
        return parse_kbars(symbol, kbars, day)

    def unsubscribe(self, symbol: str) -> None:
        symbol = symbol.strip().upper()
        code = symbol.partition(".")[0]
        entry = self._subscriptions.get(code)
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
        self._subscriptions.pop(code, None)

    def close(self) -> None:
        self._closing = True
        api, self._api = self._api, None
        self._subscriptions.clear()
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
        entry = self._subscriptions.get(str(tick.code))
        if entry is None or _value(exchange) != _value(entry[1].exchange):
            return
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
                "symbol": entry[0],
                "price": price,
                "change_pct": change_pct,
                "total_volume": total_volume,
                "volume": trade_volume,
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
