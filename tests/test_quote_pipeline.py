import json
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from quant_signal.adapters.rest.main import create_app
from quant_signal.application.quote_cache import parse_symbols
from quant_signal.infrastructure.providers.shioaji_feed import ShioajiFeed
from quant_signal.settings import get_settings
from quant_signal.workers.quotes import MinuteBuffer


def tick(price=100, second=1, total=10):
    stamp = datetime(2026, 11, 26, 1, 0, second, tzinfo=UTC)
    return dict(symbol="2330.TW", price=price, total_volume=total, trade_time=stamp.isoformat())


def test_symbol_admission_shape_and_duplicates():
    assert parse_symbols("2330.tw,2330.TW,6488.TWO") == ("2330.TW", "6488.TWO")
    for invalid in (
        "",
        "2330",
        "2330.TW,*",
        "2330.TW;KEY",
        ",".join(f"{code}.TW" for code in range(1000, 1026)),
    ):
        with pytest.raises(ValueError):
            parse_symbols(invalid)


def test_minutes_ignore_out_of_order_and_keep_separate_minutes():
    buffer = MinuteBuffer()
    buffer.add(tick(100, 1))
    buffer.add(tick(105, 3, 20))
    buffer.add(tick(90, 2, 15))
    row = next(iter(buffer.rows.values()))
    assert (row["open"], row["high"], row["low"], row["close"]) == (100, 105, 100, 105)
    bar = MinuteBuffer.bar(row)
    assert bar.volume == 10 and not bar.official
    buffer.add({**tick(), "trade_time": "2026-11-26T01:01:00+00:00"})
    assert len(buffer.rows) == 2


def test_minute_volume_counts_the_first_trade_of_the_minute():
    buffer = MinuteBuffer()
    # A thin stock with one 3-lot trade in the minute must not show zero volume.
    buffer.add({**tick(100, 5, 23), "volume": 3})
    row = next(iter(buffer.rows.values()))
    assert MinuteBuffer.bar(row).volume == 3
    assert buffer.candle("2330.TW")["volume"] == 3000
    buffer.add({**tick(101, 9, 25), "volume": 2})
    assert MinuteBuffer.bar(row).volume == 5
    # The latest quote follows the newest trade.
    assert buffer.latest["2330.TW"].price == 101 and buffer.latest["2330.TW"].volume == 25


def test_live_candle_preserves_ohlc_after_persistence_clears_dirty_rows():
    buffer = MinuteBuffer()
    buffer.add(tick(100, 1, 10))
    buffer.add(tick(105, 3, 20))
    buffer.rows.clear()
    buffer.add(tick(102, 4, 25))
    candle = buffer.candle("2330.TW")
    assert (candle["open"], candle["high"], candle["low"], candle["close"]) == (100, 105, 100, 102)
    assert candle["volume"] == 15000
    assert candle["is_partial"]


def test_disabled_quotes_are_explicit_and_do_not_contact_redis(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "quotes_enabled", False)
    monkeypatch.setattr(settings, "api_auth_enabled", False)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/quotes/stream?symbols=2330.TW")
        assert response.status_code == 200
        payload = json.loads(response.text.split("data: ")[1].strip())
        assert payload == {"status": "disabled", "quotes": []}
        assert client.get("/api/v1/quotes/stream?symbols=*").status_code == 422
        invalid_time = "/api/v1/quotes/minutes?symbol=2330.TW&before=2026-11-26"
        assert client.get(invalid_time).status_code == 422


def test_quote_stream_requires_authentication(monkeypatch):
    monkeypatch.setattr(get_settings(), "api_auth_enabled", True)
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/quotes/stream?symbols=2330.TW").status_code == 401


def test_sdk_callback_is_json_safe_and_filters_trial_odd_lot_and_wrong_market(monkeypatch):
    events = []
    contract = SimpleNamespace(exchange="TSE")
    callbacks = SimpleNamespace(
        set_on_tick_stk_v1_callback=lambda callback: None,
        set_on_tick_fop_v1_callback=lambda callback: None,
        set_event_callback=lambda callback: None,
        subscribe=lambda *args, **kwargs: None,
        unsubscribe=lambda *args, **kwargs: None,
    )
    login = {}
    api = SimpleNamespace(
        quote=callbacks,
        Contracts=SimpleNamespace(Stocks={"2330": contract}),
        login=lambda **kwargs: login.update(kwargs),
        logout=lambda: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "shioaji",
        SimpleNamespace(
            Shioaji=lambda **kwargs: api,
            constant=SimpleNamespace(
                QuoteType=SimpleNamespace(Tick="tick"), QuoteVersion=SimpleNamespace(v1="v1")
            ),
        ),
    )
    feed = ShioajiFeed(get_settings(), events.append, lambda: None)
    feed.start()
    assert login["subscribe_trade"] is False
    feed.subscribe("2330.TW")
    trade = dict(
        code="2330",
        close=100,
        pct_chg=2.5,
        total_volume=20,
        datetime=datetime(2026, 11, 26, 9, 0),
        simtrade=0,
        intraday_odd=0,
        open=98.5,
        high=float("nan"),
        low=0,
    )
    feed._handle_tick("TSE", SimpleNamespace(**trade))
    feed._handle_tick("OTC", SimpleNamespace(**trade))
    feed._handle_tick("TSE", SimpleNamespace(**{**trade, "simtrade": 1}))
    feed._handle_tick("TSE", SimpleNamespace(**{**trade, "intraday_odd": 1}))
    assert len(events) == 1
    assert events[0]["change_pct"] == 0.025
    assert events[0]["trade_time"].endswith("+08:00")
    assert events[0]["day_open"] == 98.5
    assert "day_high" not in events[0] and "day_low" not in events[0]
    assert json.loads(json.dumps(events[0]))["price"] == 100
    feed.close()


def test_quote_health_probes_distinguish_standby_from_stuck_collector():
    import time

    from quant_signal.workers.quote_health import QuoteHealth, create_health_app

    class Redis:
        ok = True

        async def ping(self):
            if not self.ok:
                raise ConnectionError
            return True

    redis = Redis()
    health = QuoteHealth()
    with TestClient(create_health_app(health, redis)) as client:
        # Standby replica: alive but not leader, so not ready.
        assert client.get("/livez").status_code == 200
        assert client.get("/readyz").status_code == 503
        health.leader = health.connected = True
        assert client.get("/health").json() == {"ready": True}
        redis.ok = False
        assert client.get("/readyz").status_code == 503
        redis.ok = True
        health.alive = time.monotonic() - 60
        assert client.get("/livez").status_code == 503
        assert client.get("/readyz").status_code == 503


def test_minutes_endpoint_shares_short_redis_cache(monkeypatch):
    from quant_signal.adapters.rest import quotes as rest_quotes

    store = {}

    class Redis:
        async def get(self, key):
            return store.get(key)

        async def set(self, key, value, nx=False, ex=None):
            if nx and key in store:
                return None
            store[key] = value
            return True

        async def eval(self, script, numkeys, key, token):
            if store.get(key) == token:
                del store[key]
                return 1
            return 0

    class Hub:
        def __init__(self, settings):
            self.redis = Redis()

        async def close(self):
            pass

    calls = []

    async def read_minutes(symbol, before, limit):
        calls.append(symbol)
        return {"symbol": symbol, "source": "shioaji", "bars": []}

    settings = get_settings()
    monkeypatch.setattr(settings, "quotes_enabled", True)
    monkeypatch.setattr(settings, "api_auth_enabled", False)
    monkeypatch.setattr(rest_quotes, "QuoteHub", Hub)
    monkeypatch.setattr(rest_quotes, "read_minutes", read_minutes)
    with TestClient(create_app()) as client:
        first = client.get("/api/v1/quotes/minutes?symbol=2330.TW")
        second = client.get("/api/v1/quotes/minutes?symbol=2330.TW")
        expected = {"symbol": "2330.TW", "source": "shioaji", "bars": []}
        assert first.json() == second.json() == expected
        assert calls == ["2330.TW"]
        assert not [key for key in store if key.endswith(":loading")]
