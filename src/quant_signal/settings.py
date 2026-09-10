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
    searxng_proxy_url: str | None = None
    searxng_proxy_key: SecretStr = SecretStr("")

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
