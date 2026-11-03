from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
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
    # How far back the whole-market snapshot is filled on first run.
    sync_market_lookback_days: int = Field(default=520, ge=30)
    kronos_url: str | None = None
    kronos_api_key: SecretStr = SecretStr("")
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:13000,http://127.0.0.1:13000"
    api_auth_enabled: bool = False
    llm_enabled: bool = False
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_model: str | None = None
    llm_api_key: SecretStr = SecretStr("local")
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_max_tokens: int = Field(default=1800, gt=0)
    llm_timeout_seconds: float = Field(default=120, gt=0)
    judge_llm_enabled: bool = False
    judge_llm_base_url: str = "http://127.0.0.1:11434/v1"
    judge_llm_model: str | None = None
    judge_llm_api_key: SecretStr = SecretStr("local")
    judge_llm_temperature: float = Field(default=0.2, ge=0, le=2)
    judge_llm_max_tokens: int = Field(default=1800, gt=0)
    judge_llm_timeout_seconds: float = Field(default=120, gt=0)
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
    dashboard_api_key: SecretStr = SecretStr("")

    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def llm_errors(self) -> list[str]:
        if not self.llm_enabled:
            return ["LLM_ENABLED"]
        missing: list[str] = []
        if not self.llm_base_url:
            missing.append("LLM_BASE_URL")
        if not self.llm_model:
            missing.append("LLM_MODEL")
        return missing


@lru_cache
def get_settings() -> Settings:
    return Settings()
