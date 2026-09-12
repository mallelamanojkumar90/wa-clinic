"""Application configuration using Pydantic Settings."""
import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # LLM Settings
    OPENROUTER_API_KEY: str = ""
    MODEL: str = "openai/gpt-4o-mini"

    # WhatsApp Cloud API Settings
    WHATSAPP_TOKEN: str = ""
    WHATSAPP_PHONE_ID: str = ""
    VERIFY_TOKEN: str = "manojkumar"
    META_APP_SECRET: Optional[str] = None  # For X-Hub-Signature-256 HMAC verification

    # Database Settings (Supabase / Postgres or local SQLite fallback)
    DATABASE_URL: Optional[str] = None

    # Google Calendar Settings
    GOOGLE_SERVICE_ACCOUNT_JSON: Optional[str] = None
    GOOGLE_CALENDAR_ID: str = "primary"
    CLINIC_TIMEZONE: str = "Asia/Kolkata"

    # Server Settings
    PORT: int = 8000
    DEBUG: bool = False

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
