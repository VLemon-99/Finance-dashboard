"""App settings, read from environment variables and the `.env` file.

Secrets (API keys) live only in `.env`, which is git-ignored. See `.env.example`.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    # Where the database lives. Default: data/app.db inside the project folder.
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'data' / 'app.db'}"

    # The single default user created at startup (F7.1). Accounts come in Phase 10.
    default_user_email: str = "owner@localhost"

    # Seconds to wait for the market data source before giving up.
    market_data_timeout_seconds: float = 10.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
