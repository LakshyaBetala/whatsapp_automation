"""Application settings, loaded from environment / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Supabase ---
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_db_url: str = ""

    # --- AiSensy ---
    aisensy_api_key: str = ""
    aisensy_api_base: str = "https://backend.aisensy.com"
    aisensy_webhook_secret: str = ""

    # --- App ---
    app_env: str = "development"
    timezone: str = "Asia/Kolkata"
    tally_agent_token: str = "change-me"
    webhook_verify_token: str = "change-me"          # Meta webhook GET handshake
    public_base_url: str = "http://localhost:8000"

    # --- Scheduling ---
    eod_digest_hour: int = 21
    eod_digest_minute: int = 0
    reminder_sweep_hour: int = 10
    reminder_sweep_minute: int = 0

    # --- AI (optional) ---
    gemini_api_key: str = ""
    anthropic_api_key: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in ("production", "prod")

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)

    @property
    def aisensy_configured(self) -> bool:
        return bool(self.aisensy_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
