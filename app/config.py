"""App settings.

Two places, on purpose:
- Secrets and machine-specific values come from environment variables and the `.env` file
  (git-ignored, see `.env.example`). That's `Settings`.
- Limits and timings you may want to tweak live in `config/settings.yaml`. That's `AppConfig`.
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
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

    # The limits/timings file (see AppConfig below).
    app_config_file: Path = PROJECT_ROOT / "config" / "settings.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# --- config/settings.yaml ---------------------------------------------------------
# `extra="forbid"` means a misspelled setting name is reported instead of silently ignored.


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WatchlistLimits(_Section):
    max_watchlists: int = Field(default=30, ge=1)
    max_items_per_list: int = Field(default=25, ge=1)


class MarketDataConfig(_Section):
    refresh_minutes: int = Field(default=15, ge=1)
    max_parallel_fetches: int = Field(default=4, ge=1, le=10)


class SearchConfig(_Section):
    max_results: int = Field(default=10, ge=1, le=25)


class AppConfig(_Section):
    watchlists: WatchlistLimits = WatchlistLimits()
    market_data: MarketDataConfig = MarketDataConfig()
    search: SearchConfig = SearchConfig()


class ConfigFileError(RuntimeError):
    pass


def load_app_config(path: Path) -> AppConfig:
    """Read config/settings.yaml. Problems stop the app with a message naming the file."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigFileError(f"Settings file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigFileError(f"{path} isn't valid YAML: {exc}") from exc
    try:
        return AppConfig.model_validate(raw or {})
    except ValidationError as exc:
        raise ConfigFileError(f"{path} has a problem:\n{exc}") from exc
