"""Application configuration using Pydantic Settings."""
import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # LLM & Audio Settings
    OPENROUTER_API_KEY: str = ""
    MODEL: str = "openai/gpt-4o-mini"
    GROQ_API_KEY: Optional[str] = None  # Fast Whisper audio transcription
    OPENAI_API_KEY: Optional[str] = None  # Fallback Whisper audio transcription

    # WhatsApp Cloud API Settings
    WHATSAPP_TOKEN: str = ""
    WHATSAPP_PHONE_ID: str = ""
    VERIFY_TOKEN: str = "manojkumar"
    META_APP_SECRET: Optional[str] = None  # For X-Hub-Signature-256 HMAC verification

    # Automated Reminders Settings
    ENABLE_REMINDERS: bool = True
    REMINDER_CHECK_INTERVAL_MINUTES: int = 15

    # Razorpay Payment Gateway Settings
    ENABLE_RAZORPAY: bool = False
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""
    ADVANCE_TOKEN_AMOUNT_INR: int = 200
    SLOT_HOLD_MINUTES: int = 15

    # Voice AI Receptionist Settings (Bolna.dev / Vobiz)
    ENABLE_VOICE_AI: bool = True
    VOICE_API_SECRET: Optional[str] = None  # Optional shared secret header for Bolna webhook calls

    # Database Settings (Supabase / Postgres or local SQLite fallback)
    DATABASE_URL: Optional[str] = None

    # Google Calendar Settings
    GOOGLE_SERVICE_ACCOUNT_JSON: Optional[str] = None
    GOOGLE_CALENDAR_ID: str = "primary"
    CLINIC_TIMEZONE: str = "Asia/Kolkata"

    # Server & Dashboard Settings
    PORT: int = 8000
    DEBUG: bool = False
    DASHBOARD_PIN: str = "1234"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def is_postgres(self) -> bool:
        return bool(self.DATABASE_URL and self.DATABASE_URL.startswith(("postgresql", "postgres")))

    @property
    def sqlalchemy_database_url(self) -> str:
        if not self.DATABASE_URL:
            # Fallback to local SQLite
            db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "clinic.db"))
            return f"sqlite:///{db_path}"
        
        # Ensure psycopg driver is specified if postgres:// is provided (e.g. from Render or Supabase)
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://") and "+psycopg" not in url:
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

settings = Settings()
