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

    # --- OpenWA (WhatsApp Node microservice, wa_service/ - port 3001) ---
    openwa_url: str = "http://localhost:3001"
    # Optional SECOND wa_service session (our company/platform number) used
    # for owner-facing messages: digest, alerts, renewal notices. Empty =
    # everything goes through the shop's own number.
    platform_wa_url: str = ""
    # Where an OWNER's support request (TEAM <msg>) is forwarded (your product
    # team's WhatsApp number). Empty = the bot just acknowledges. Customer
    # issues are forwarded to their own shop owner, not here.
    product_team_number: str = ""

    # --- App ---
    app_env: str = "development"
    # Release version of THIS build. Bump on every shipped zip. The dashboard
    # compares it against the newest row in app_releases (Supabase) and shows
    # an update banner when a newer version exists - Tally-style update notice.
    app_version: str = "1.3.0"
    timezone: str = "Asia/Kolkata"
    tally_agent_token: str = "change-me"
    webhook_verify_token: str = "change-me"          # Meta webhook GET handshake
    public_base_url: str = "http://localhost:8000"

    # --- Sending safety ---
    # Max customer reminders per business per day. Backlog drips out over
    # following days - protects a fresh WhatsApp session from bulk-send
    # ban patterns and customers from a day-1 blast.
    daily_reminder_cap: int = 25
    # Randomised gap (seconds) between consecutive sends in a sweep so traffic
    # looks human, not a burst. Keep the window wide; the daily cap bounds total.
    send_gap_min_s: float = 12.0
    send_gap_max_s: float = 40.0

    # --- Scheduling ---
    eod_digest_hour: int = 22   # 10 PM IST - owner's end-of-day summary via the bot
    eod_digest_minute: int = 0
    reminder_sweep_hour: int = 10
    reminder_sweep_minute: int = 0

    # --- Selective job control (per-deployment) ---
    # Bot host sets ENABLE_REMINDER_SWEEP=false (reminders go from the shop number).
    # Father's laptop sets ENABLE_EOD_DIGEST=false (digest comes from the bot number).
    enable_eod_digest: bool = True
    enable_reminder_sweep: bool = True
    enable_subscription_check: bool = True

    # --- Cross-laptop send queue (wa_outbox) ---
    # Rule: a PARTY only ever hears from the SMB owner's own shop number, never
    # the bot number. The bot deployment sets SEND_VIA_OUTBOX=true so its
    # customer-facing sends (REMIND/MSG/BILL/PAID confirm) are queued in
    # Supabase; the shop deployment (ENABLE_OUTBOX_SEND=true) delivers the
    # queue from the shop number every minute. Owner-facing sends (digest,
    # alerts, bot replies) stay direct on the bot number.
    send_via_outbox: bool = False       # true ONLY on the bot laptop
    enable_outbox_send: bool = True     # false ONLY on the bot laptop

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
