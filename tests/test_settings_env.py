"""Blank environment variables must behave like unset ones."""

import pytest

from quant_signal.settings import Settings

BLANK_IN_PRODUCTION = (
    "SYNC_ENABLED",
    "SYNC_LOOKBACK_DAYS",
    "SYNC_DAILY_TIME",
    "SYNC_MARKET_LOOKBACK_DAYS",
)


def test_blank_variables_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # Coolify passed these four as "" to the worker after its compose file stopped
    # referencing them, and the worker crashed on start.
    for name in BLANK_IN_PRODUCTION:
        monkeypatch.setenv(name, "")

    settings = Settings(_env_file=None)

    assert settings.sync_enabled is True
    assert settings.sync_lookback_days == 500
    assert settings.sync_daily_time == "15:30,21:45"
    assert settings.sync_market_lookback_days == 520


def test_set_variables_still_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNC_ENABLED", "false")
    monkeypatch.setenv("SYNC_LOOKBACK_DAYS", "120")

    settings = Settings(_env_file=None)

    assert settings.sync_enabled is False
    assert settings.sync_lookback_days == 120
