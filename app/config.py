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

    # The trusted sources file for reports (see SourcesConfig below, SPEC F4.4).
    sources_config_file: Path = PROJECT_ROOT / "config" / "sources.yaml"

    # Seconds to wait for a news feed or the SEC before giving up.
    source_timeout_seconds: float = 20.0

    # The SEC requires a contact in every request: "Your Name your-email@example.com".
    sec_user_agent: str = ""

    # Gemini API key from Google AI Studio. Empty = AI summaries are switched off.
    gemini_api_key: str = ""


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


class OptionsConfig(_Section):
    # Risk-free rate (percent per year) used when the 13-week T-bill rate can't be fetched.
    fallback_risk_free_rate_percent: float = Field(default=4.0, ge=0, le=20)
    # Yahoo gives a tiny placeholder IV for strikes that barely trade. IVs below this
    # (percent) are shown as missing and no greeks are calculated from them.
    min_implied_volatility_percent: float = Field(default=1.0, ge=0, le=50)


class SearchConfig(_Section):
    max_results: int = Field(default=10, ge=1, le=25)


class ReportsConfig(_Section):
    # A report covers the time since the previous one, but never more than this (F4.14).
    max_days: int = Field(default=7, ge=1, le=31)
    # Headlines at least this similar (0 to 1) count as the same story (F4.12).
    duplicate_headline_similarity: float = Field(default=0.85, ge=0.5, le=1.0)
    # ...if they were published within this many hours of each other.
    duplicate_window_hours: int = Field(default=72, ge=1, le=336)
    # Sentiment weight per source type (F4.6).
    sentiment_weights: dict[str, float] = {
        "filing": 1.0,
        "news": 1.0,
        "blog": 0.8,
        "youtube": 0.7,
        "x": 0.5,
        "reddit": 0.3,
    }


class AIConfig(_Section):
    # Gemini model. "gemini-flash-latest" always points at Google's current Flash model.
    model: str = Field(default="gemini-flash-latest", min_length=1, max_length=60)
    timeout_seconds: int = Field(default=120, ge=10, le=600)
    # Items summarized together in one AI call, and the most text per call (characters).
    max_items_per_call: int = Field(default=20, ge=1, le=100)
    max_chars_per_call: int = Field(default=400_000, ge=10_000, le=2_000_000)
    # Most statements per report section.
    max_developments: int = Field(default=6, ge=1, le=20)
    max_rumors: int = Field(default=4, ge=0, le=20)


class AppConfig(_Section):
    watchlists: WatchlistLimits = WatchlistLimits()
    market_data: MarketDataConfig = MarketDataConfig()
    options: OptionsConfig = OptionsConfig()
    search: SearchConfig = SearchConfig()
    reports: ReportsConfig = ReportsConfig()
    ai: AIConfig = AIConfig()


# --- config/sources.yaml (trusted sources, SPEC F4.4) ---------------------------------


class GoogleNewsConfig(_Section):
    enabled: bool = True
    query: str = '"{company}" OR "{ticker} stock"'
    query_without_company: str = '"{ticker} stock"'


class FeedConfig(_Section):
    enabled: bool = True


class NewsConfig(_Section):
    google_news: GoogleNewsConfig = GoogleNewsConfig()
    yahoo_finance: FeedConfig = FeedConfig()
    trusted_outlets_only: bool = False
    trusted_outlets: list[str] = []


class EdgarConfig(_Section):
    enabled: bool = True
    forms: list[str] = [
        "8-K",
        "10-Q",
        "10-K",
        "4",
        "SC 13D",
        "SC 13G",
        "SCHEDULE 13D",
        "SCHEDULE 13G",
    ]
    insider_lookback_days: int = Field(default=90, ge=1, le=365)
    max_text_chars: int = Field(default=150_000, ge=1_000, le=1_000_000)


class SourcesConfig(_Section):
    news: NewsConfig = NewsConfig()
    sec_edgar: EdgarConfig = EdgarConfig()


class ConfigFileError(RuntimeError):
    pass


def _load_yaml(path: Path, model: type[BaseModel], what: str):
    """Read a YAML config file. Problems stop the app with a message naming the file."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigFileError(f"{what} file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigFileError(f"{path} isn't valid YAML: {exc}") from exc
    try:
        return model.model_validate(raw or {})
    except ValidationError as exc:
        raise ConfigFileError(f"{path} has a problem:\n{exc}") from exc


def load_app_config(path: Path) -> AppConfig:
    """Read config/settings.yaml."""
    return _load_yaml(path, AppConfig, "Settings")


def load_sources_config(path: Path) -> SourcesConfig:
    """Read config/sources.yaml."""
    return _load_yaml(path, SourcesConfig, "Sources")
