from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["openai_compatible", "anthropic"]
AnthropicEffort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # A blank variable means "not set" (same as ${VAR:-default} in the compose
        # files). Coolify keeps passing variables a compose file stopped referencing,
        # with an empty value; without this a blank SYNC_ENABLED crashes the worker.
        env_ignore_empty=True,
    )

    database_url: str = "postgresql+asyncpg://quant:quant@127.0.0.1:15432/quant_signal"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=18100, ge=1, le=65535)
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=18101, ge=1, le=65535)
    mcp_transport: str = "streamable-http"
    mcp_stateless_http: bool = True
    backtest_poll_seconds: float = Field(default=2.0, gt=0)
    sync_enabled: bool = True
    sync_poll_seconds: float = Field(default=5.0, gt=0)
    sync_lookback_days: int = Field(default=500, ge=60)
    sync_max_attempts: int = Field(default=3, ge=1)
    sync_stale_after_seconds: float = Field(default=1800, gt=0)
    # Taipei local times (24h, comma separated) after which the daily refresh is
    # queued. The second run picks up reports the exchanges publish late in the
    # evening (credit balances).
    sync_daily_time: str = Field(
        default="15:30,21:45", pattern=r"^\d{2}:\d{2}(,\d{2}:\d{2})*$"
    )
    # News (消息面) job in the ingest service: Taipei times, like sync_daily_time.
    news_sync_enabled: bool = True
    news_sync_time: str = Field(
        default="08:30,14:10", pattern=r"^\d{2}:\d{2}(,\d{2}:\d{2})*$"
    )
    news_sync_max_symbols: int = Field(default=60, ge=0)
    # Seconds between per-company queries to Google News (be polite).
    news_sync_delay_seconds: float = Field(default=1.5, ge=1.0)
    # Comma-separated RSS/Atom feed URLs read whole and matched to symbols by code/name.
    news_feed_urls: str = ""
    # Claude model used only to classify headlines (cheap on purpose; the analyst and
    # debate models are unchanged). Empty falls back to ANTHROPIC_MODEL.
    news_anthropic_model: str | None = "claude-haiku-5-5"
    # How far back the whole-market snapshot is filled on first run.
    sync_market_lookback_days: int = Field(default=520, ge=30)
    # Official TWSE/TPEx events, risk flags and monthly revenue (run by the sync worker).
    official_events_enabled: bool = True
    official_events_material_minutes: int = Field(default=30, ge=5, le=240)
    official_events_retry_minutes: float = Field(default=10.0, gt=0)
    # Whole-market multi-factor screener snapshot (computed by the sync worker, never per request).
    screener_enabled: bool = True
    screener_retention_sessions: int = Field(default=60, ge=1)
    screener_backfill_sessions: int = Field(default=5, ge=1)
    # Whole-market holder data: TDCC weekly shareholding distribution (大戶/散戶持股) and
    # TWSE/TPEx shares outstanding + foreign holding (also run by the sync worker).
    holders_tdcc_enabled: bool = True
    holders_foreign_enabled: bool = True
    # Weekdays re-checked for foreign holding on each run (only missing ones are fetched).
    holders_foreign_days: int = Field(default=5, ge=1, le=30)
    holders_retry_minutes: float = Field(default=10.0, gt=0)
    kronos_url: str | None = None
    kronos_api_key: SecretStr = SecretStr("")
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:13000,http://127.0.0.1:13000"
    api_auth_enabled: bool = False
    llm_enabled: bool = False
    # "openai_compatible": any chat-completions server (local vLLM / Ollama / hosted).
    # "anthropic": Claude through the official Anthropic SDK (ANTHROPIC_* settings below).
    llm_provider: LLMProvider = "openai_compatible"
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_model: str | None = None
    llm_api_key: SecretStr = SecretStr("local")
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_max_tokens: int = Field(default=1800, gt=0)
    llm_timeout_seconds: float = Field(default=120, gt=0)
    judge_llm_enabled: bool = False
    judge_llm_provider: LLMProvider = "openai_compatible"
    judge_llm_base_url: str = "http://127.0.0.1:11434/v1"
    judge_llm_model: str | None = None
    judge_llm_api_key: SecretStr = SecretStr("local")
    judge_llm_temperature: float = Field(default=0.2, ge=0, le=2)
    judge_llm_max_tokens: int = Field(default=1800, gt=0)
    judge_llm_timeout_seconds: float = Field(default=120, gt=0)
    # Claude (LLM_PROVIDER / JUDGE_LLM_PROVIDER = "anthropic"). The OpenAI-style
    # LLM_MODEL / LLM_TEMPERATURE / LLM_MAX_TOKENS settings do not apply to Claude:
    # current Claude models take no temperature, and max_tokens also covers thinking.
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-opus-5-5"
    judge_anthropic_model: str | None = None  # defaults to anthropic_model
    anthropic_effort: AnthropicEffort = "medium"
    anthropic_max_tokens: int = Field(default=16000, gt=0)
    anthropic_timeout_seconds: float = Field(default=600, gt=0)
    searxng_proxy_url: str | None = None
    searxng_proxy_key: SecretStr = SecretStr("")
    dashboard_password: SecretStr = SecretStr("")
    # Google sign-in (OAuth authorization-code flow with PKCE, handled by the API).
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    # Where Google sends the browser back; must match the console exactly, e.g.
    # https://example.com/api/v1/auth/google/callback
    google_redirect_uri: str = ""
    # Where to send the browser after login (the dashboard origin); "" = same origin.
    frontend_url: str = ""
    # Signs the short-lived OAuth state cookie. Set a long random value in production.
    session_secret: SecretStr = SecretStr("")
    session_ttl_days: int = Field(default=30, ge=1, le=365)
    # Comma-separated allow-lists, applied after Google confirms the email. Both empty
    # means any verified Google account may sign in.
    auth_allowed_emails: str = ""
    auth_allowed_email_domains: str = ""
    # Daily per-user limits on the expensive endpoints (API keys are exempt).
    quota_llm_per_day: int = Field(default=20, ge=0)
    quota_forecast_per_day: int = Field(default=100, ge=0)
    quota_sync_per_day: int = Field(default=50, ge=0)
    quota_backtest_per_day: int = Field(default=50, ge=0)
    watchlist_max_items: int = Field(default=25, ge=1, le=500)
    dashboard_api_key: SecretStr = SecretStr("")
    quotes_enabled: bool = False
    quotes_redis_url: SecretStr = SecretStr("redis://127.0.0.1:6379/0")
    quotes_max_symbols: int = Field(default=200, ge=1, le=200)
    quotes_max_connections_per_user: int = Field(default=5, ge=1, le=50)
    shioaji_api_key: SecretStr = SecretStr("")
    shioaji_secret_key: SecretStr = SecretStr("")
    shioaji_simulation: bool = False
    # Official 1-minute bars (Shioaji kbars) fill the minutes the live feed missed.
    quotes_backfill_enabled: bool = True
    # Taipei time after which the day's minutes are replaced with official kbars.
    quotes_backfill_after_close: str = Field(
        default="14:35", pattern=r"^([01]\d|2[0-3]):[0-5]\d$"
    )
    # Shioaji allows 270 kbars queries during the session; keep the rest for manual use.
    quotes_backfill_intraday_max: int = Field(default=150, ge=0, le=250)
    # After the close, collect the whole listed + OTC market (~2,000 symbols, watched
    # ones first). At 40 queries / 10 s that takes ~9 minutes and ~30 MB of traffic.
    quotes_backfill_whole_market: bool = True
    quotes_backfill_max_symbols: int = Field(default=2500, ge=1, le=4000)
    # Stop history queries and snapshots when today's remaining Shioaji traffic
    # (api.usage().remaining_bytes) drops below this reserve, so live quotes keep flowing.
    quotes_usage_reserve_mb: int = Field(default=100, ge=0, le=10000)
    # Whole-market snapshots (latest price for every stock) every N minutes in the session.
    quotes_snapshot_minutes: int = Field(default=10, ge=0, le=60)
    # Keep this many months of 1-minute bars (0 keeps everything).
    minute_bars_retention_months: int = Field(default=0, ge=0, le=240)
    # TAIFEX derivatives (futures/options positioning, P/C ratio, large traders, single-stock
    # futures, VIX), fetched by the sync worker once the day's reports are published.
    derivatives_enabled: bool = True
    # Sessions back-filled gradually and off-session; 0 disables the back-fill.
    derivatives_backfill_sessions: int = Field(default=120, ge=0, le=500)
    # Taipei time from which the day's reports are polled (day session closes 13:45).
    derivatives_after_close: str = Field(default="15:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    # TAIFEX redirects clients that burst, so keep the spacing generous.
    derivatives_request_interval_seconds: float = Field(default=6.0, ge=1.0)
    derivatives_requests_per_tick: int = Field(default=4, ge=1, le=50)
    derivatives_retry_minutes: float = Field(default=15.0, gt=0)

    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def llm_errors(self) -> list[str]:
        if not self.llm_enabled:
            return ["LLM_ENABLED"]
        missing: list[str] = []
        if self.llm_provider == "anthropic":
            if not self.anthropic_api_key.get_secret_value():
                missing.append("ANTHROPIC_API_KEY")
            if not self.anthropic_model:
                missing.append("ANTHROPIC_MODEL")
            return missing
        if not self.llm_base_url:
            missing.append("LLM_BASE_URL")
        if not self.llm_model:
            missing.append("LLM_MODEL")
        return missing


@lru_cache
def get_settings() -> Settings:
    return Settings()
