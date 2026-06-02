"""Environment-driven settings with safe local fallbacks.

Every external dependency (DB, blob storage, OCR, email, SMS) degrades to a
local/console implementation when no credentials are present, so the app runs
end-to-end on a laptop with an empty .env while staying faithful to the
production architecture (Neon + R2 + vision-LLM + Resend/Twilio).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root (…/Accountant). Used for sqlite + local storage paths.
ROOT = Path(__file__).resolve().parent.parent

# On Vercel the filesystem is read-only except /tmp, so the local sqlite/storage
# fallbacks live in /tmp there (ephemeral — set DATABASE_URL=Neon + R2 for prod).
ON_VERCEL = bool(os.getenv("VERCEL"))
WRITABLE_ROOT = Path("/tmp") if ON_VERCEL else ROOT


def _default_database_url() -> str:
    return f"sqlite:///{(WRITABLE_ROOT / 'local.db').as_posix()}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- core ---
    env: str = "development"
    secret_key: str = "dev-insecure-change-me-please-0123456789"
    base_url: str = "http://127.0.0.1:8000"

    # --- database ---
    database_url: str = Field(default_factory=_default_database_url)

    # --- storage ---
    storage_backend: str = ""  # "local" | "r2" | "" (auto)
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "mtd-receipts"
    r2_endpoint: str = ""

    # --- OCR ---
    ocr_provider: str = ""  # "stub"|"gemini"|"groq"|"openai"|"anthropic"|"" (auto)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"  # env GEMINI_MODEL (change if 404s)
    groq_api_key: str = ""
    groq_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"  # env GROQ_MODEL
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # --- email ---
    email_provider: str = ""  # "console" | "smtp" | "resend" | "" (auto)
    resend_api_key: str = ""
    email_from: str = "MTD App <noreply@example.com>"
    # SMTP (e.g. IONOS, Gmail, etc.) — an alternative to Resend.
    mail_server: str = ""          # env MAIL_SERVER, e.g. smtp.ionos.co.uk
    mail_port: int = 587           # env MAIL_PORT (587 STARTTLS, 465 SSL)
    email_user: str = ""           # env EMAIL_USER
    email_pass: str = ""           # env EMAIL_PASS
    mail_default_sender: str = ""  # env MAIL_DEFAULT_SENDER, e.g. "JrDev <noreply@jr-dev.uk>"

    # --- SMS ---
    sms_provider: str = ""  # "console" | "twilio" | "" (auto)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from: str = ""

    # --- auth / dev convenience ---
    dev_show_otp: bool = True
    seed_demo_users: bool = True
    otp_ttl_minutes: int = 15
    session_days: int = 45

    # ---- effective provider resolution (auto when not explicitly set) ----
    @property
    def is_dev(self) -> bool:
        return self.env.lower().startswith("dev")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def effective_storage(self) -> str:
        if self.storage_backend:
            return self.storage_backend
        if self.r2_access_key_id and self.r2_secret_access_key and self.r2_endpoint:
            return "r2"
        return "local"

    @property
    def effective_ocr(self) -> str:
        if self.ocr_provider:
            return self.ocr_provider
        if self.gemini_api_key:
            return "gemini"
        if self.groq_api_key:
            return "groq"
        if self.openai_api_key:
            return "openai"
        if self.anthropic_api_key:
            return "anthropic"
        return "stub"

    @property
    def effective_email(self) -> str:
        if self.email_provider:
            return self.email_provider
        if self.mail_server and self.email_user and self.email_pass:
            return "smtp"
        if self.resend_api_key:
            return "resend"
        return "console"

    @property
    def mail_from(self) -> str:
        return self.mail_default_sender or self.email_from or self.email_user

    @property
    def effective_sms(self) -> str:
        if self.sms_provider:
            return self.sms_provider
        return "twilio" if (self.twilio_account_sid and self.twilio_auth_token) else "console"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


# ============================================================================
#  MTD tax configuration — "keep thresholds, deadlines & box mapping in config,
#  not code" (architecture plan §7 / risk #11). Amounts in PENNIES.
# ============================================================================

# Mandation thresholds keyed by the tax-year-START year they first apply to.
# Mandation is tested on the *previous* qualifying-income return.
#   2026/27 mandation uses 2024/25 vs £50k; 2027/28 uses 2025/26 vs £30k;
#   2028/29 uses 2026/27 vs £20k.
MTD_THRESHOLDS_MINOR: dict[int, int] = {
    2026: 50_000_00,
    2027: 30_000_00,
    2028: 20_000_00,
}
# Below the earliest known year, assume the highest (£50k) line still applies.
DEFAULT_THRESHOLD_MINOR = 50_000_00

# UK tax year boundaries (month, day).
TAX_YEAR_START = (4, 6)   # 6 April
TAX_YEAR_END = (4, 5)     # 5 April

# Quarterly update deadlines (month, day) — periods end on the 5th, due the 7th.
# Q3/Q4/Final fall in the *following* calendar year(s) relative to year start.
QUARTER_DEADLINES = [
    ("Q1", (8, 7), 0),    # 7 Aug, same year as 6-Apr start
    ("Q2", (11, 7), 0),   # 7 Nov
    ("Q3", (2, 7), 1),    # 7 Feb (+1 yr)
    ("Q4", (5, 7), 1),    # 7 May (+1 yr)
]
FINAL_DECLARATION = ((1, 31), 2)  # 31 Jan, +2 yrs after the 6-Apr start
