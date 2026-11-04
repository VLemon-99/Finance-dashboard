"""App settings.

Two places, on purpose:
- Secrets and machine-specific values come from environment variables and the `.env` file
  (git-ignored, see `.env.example`). That's `Settings`.
- Limits and timings you may want to tweak live in `config/settings.yaml`. That's `AppConfig`.
"""

from datetime import date
from datetime import time as clock_time
from functools import lru_cache
from pathlib import Path
from typing import Literal

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

    # FRED (St. Louis Fed) API key for economic release dates. Empty = those are missing.
    fred_api_key: str = ""

    # Fed meeting dates for the calendar (see FomcConfig below).
    fomc_dates_file: Path = PROJECT_ROOT / "config" / "fomc_dates.yaml"


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
    # One-off market closures besides weekends and regular NYSE holidays (those are
    # calculated in app/market_calendar.py).
    extra_closed_days: frozenset[date] = frozenset()


class OptionsConfig(_Section):
    # Risk-free rate (percent per year) used when the 13-week T-bill rate can't be fetched.
    fallback_risk_free_rate_percent: float = Field(default=4.0, ge=0, le=20)
    # Yahoo gives a tiny placeholder IV for strikes that barely trade. IVs below this
    # (percent) are shown as missing and no greeks are calculated from them.
    min_implied_volatility_percent: float = Field(default=1.0, ge=0, le=50)


class SearchConfig(_Section):
    max_results: int = Field(default=10, ge=1, le=25)


class ReportsConfig(_Section):
    # Every report covers this many days back (F4.14, owner 2026-11-04).
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
    # YouTube (SPEC F4.5): the most new videos the AI watches in one run (each video is
    # watched once, ever), how much of each video it watches (minutes, from the start), and
    # how many videos it watches at the same time.
    max_videos_per_run: int = Field(default=10, ge=0, le=100)
    max_video_minutes: int = Field(default=60, ge=1, le=180)
    parallel_videos: int = Field(default=3, ge=1, le=8)


class ScheduleConfig(_Section):
    # Automatic reports for every watchlist on weekdays (SPEC F4.9). Off by default
    # (owner 2026-11-04: reports are started by hand for now).
    enabled: bool = False
    time: clock_time = clock_time(7, 30)  # US Eastern


Impact = Literal["high", "medium"]


class MacroRelease(_Section):
    """One economic release whose dates come from FRED's release calendar (SPEC F5.2)."""

    name: str = Field(min_length=1, max_length=80)  # shown in the calendar
    fred_release_id: int = Field(ge=1)
    time: clock_time = clock_time(8, 30)  # US Eastern: the usual release time
    impact: Impact = "medium"


class CalendarConfig(_Section):
    # The list view shows this many days from today (SPEC F5.3).
    list_days: int = Field(default=14, ge=1, le=90)
    # Dates are checked again after this many hours, or this many minutes after a failure.
    refresh_hours: int = Field(default=24, ge=1, le=168)
    retry_minutes: int = Field(default=15, ge=1, le=1440)
    # How far ahead earnings dates are fetched.
    earnings_days_ahead: int = Field(default=90, ge=7, le=365)
    # Impact tags (SPEC F5.2), owner 2026-11-04.
    earnings_impact: Impact = "high"
    fomc_decision_impact: Impact = "high"
    fomc_minutes_impact: Impact = "medium"
    # Events shown on the home page (SPEC F5.6).
    home_events: int = Field(default=3, ge=1, le=10)
    macro_releases: list[MacroRelease] = [
        MacroRelease(name="CPI (consumer prices)", fred_release_id=10, impact="high"),
        MacroRelease(name="Jobs report (employment situation)", fred_release_id=50, impact="high"),
        MacroRelease(name="PCE (personal income and outlays)", fred_release_id=54),
        MacroRelease(name="GDP", fred_release_id=53),
    ]


class AppConfig(_Section):
    watchlists: WatchlistLimits = WatchlistLimits()
    market_data: MarketDataConfig = MarketDataConfig()
    options: OptionsConfig = OptionsConfig()
    search: SearchConfig = SearchConfig()
    reports: ReportsConfig = ReportsConfig()
    ai: AIConfig = AIConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    calendar: CalendarConfig = CalendarConfig()


# --- config/fomc_dates.yaml (Fed meetings, SPEC F5.2) ---------------------------------


class FomcMeeting(_Section):
    decision: date  # the meeting's last day, when the rate decision is announced
    minutes: date | None = None  # when the minutes come out; None = three weeks later


class FomcConfig(_Section):
    decision_time: clock_time = clock_time(14, 0)  # US Eastern
    minutes_time: clock_time = clock_time(14, 0)
    meetings: list[FomcMeeting] = []


def load_fomc_config(path: Path) -> FomcConfig:
    """Read config/fomc_dates.yaml."""
    return _load_yaml(path, FomcConfig, "Fed meeting dates")


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


class BlogFeed(_Section):
    name: str = Field(min_length=1, max_length=100)  # shown in reports
    feed: str = Field(pattern=r"^https?://\S+$", max_length=2000)  # RSS or Atom address


class BlogsConfig(_Section):
    enabled: bool = True
    feeds: list[BlogFeed] = []


class YouTubeChannel(_Section):
    name: str = Field(min_length=1, max_length=100)  # shown in reports
    # The channel's id: "UC" followed by 22 letters, digits, "-" or "_".
    channel_id: str = Field(pattern=r"^UC[A-Za-z0-9_-]{22}$")


class YouTubeConfig(_Section):
    enabled: bool = True
    # Only videos published in the last this-many days are used (SPEC F4.5).
    lookback_days: int = Field(default=7, ge=1, le=31)
    channels: list[YouTubeChannel] = []


class SourcesConfig(_Section):
    news: NewsConfig = NewsConfig()
    sec_edgar: EdgarConfig = EdgarConfig()
    blogs: BlogsConfig = BlogsConfig()
    youtube: YouTubeConfig = YouTubeConfig()
    # Extra names that count as a mention of a ticker in blog posts and videos, e.g.
    # GOOGL: [Google]. The ticker and the SEC's company name always count.
    also_known_as: dict[str, list[str]] = {}


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
