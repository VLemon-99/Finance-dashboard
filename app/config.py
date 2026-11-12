"""App settings.

Two places, on purpose:
- Secrets and machine-specific values come from environment variables and the `.env` file
  (git-ignored, see `.env.example`). That's `Settings`.
- Limits and timings you may want to tweak live in `config/settings.yaml`. That's `AppConfig`.
"""

import re
from datetime import date
from datetime import time as clock_time
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.tickers import InvalidTicker, normalize_ticker

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

    # What each stock's valuation is compared with (see BenchmarksConfig below, SPEC F4.7).
    benchmarks_file: Path = PROJECT_ROOT / "config" / "benchmarks.yaml"

    # Funds followed and unusual options thresholds (see InstitutionalConfig, SPEC F4.8).
    institutional_file: Path = PROJECT_ROOT / "config" / "institutional.yaml"

    # X (Twitter) posts in reports (see XConfig below, SPEC F4.3). Off unless switched on.
    x_config_file: Path = PROJECT_ROOT / "config" / "x.yaml"

    # X API "Bearer Token" (developer console -> your app -> Keys and tokens). Empty = X off.
    x_bearer_token: str = ""

    # The macro dashboard's indicators (see MacroConfig below, SPEC F6).
    macro_config_file: Path = PROJECT_ROOT / "config" / "macro.yaml"

    # Login settings: password hashing cost (see AccountsConfig below, SPEC F7.2).
    accounts_config_file: Path = PROJECT_ROOT / "config" / "accounts.yaml"


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


# --- config/benchmarks.yaml (what valuations are compared with, SPEC F4.7) ------------

# Yahoo's 11 sectors: the key it uses -> the name it shows.
YAHOO_SECTORS = {
    "basic-materials": "Basic Materials",
    "communication-services": "Communication Services",
    "consumer-cyclical": "Consumer Cyclical",
    "consumer-defensive": "Consumer Defensive",
    "energy": "Energy",
    "financial-services": "Financial Services",
    "healthcare": "Healthcare",
    "industrials": "Industrials",
    "real-estate": "Real Estate",
    "technology": "Technology",
    "utilities": "Utilities",
}


def slug(text: str) -> str:
    """'Consumer Cyclical' -> 'consumer-cyclical'; 'Software - Infrastructure' ->
    'software-infrastructure' (the form Yahoo uses for its sector and industry keys)."""
    return "-".join(re.findall(r"[a-z0-9]+", text.lower().replace("&", " ")))


def _tickers(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    try:
        return list(dict.fromkeys(normalize_ticker(v) for v in values))
    except InvalidTicker as exc:
        raise ValueError(f"'{exc}' isn't a valid ticker") from exc


class TickerBenchmarks(_Section):
    """Changes to the automatic comparison groups for one ticker. Leave out what's fine."""

    # One of Yahoo's 11 sectors, by name ("Technology") or key ("technology").
    sector: str | None = None
    # A Yahoo industry name or key, e.g. "Software - Infrastructure".
    industry: str | None = None
    # Your own peer tickers, used instead of the industry's largest companies.
    peers: list[str] | None = Field(default=None, min_length=1, max_length=30)
    # The Damodaran industry to compare with, e.g. "Software (System & Application)".
    damodaran_industry: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("sector")
    @classmethod
    def _known_sector(cls, value: str | None) -> str | None:
        if value is None:
            return None
        key = slug(value)
        if key not in YAHOO_SECTORS:
            names = ", ".join(YAHOO_SECTORS.values())
            raise ValueError(f"'{value}' isn't one of Yahoo's sectors: {names}")
        return key

    @field_validator("industry")
    @classmethod
    def _industry_key(cls, value: str | None) -> str | None:
        return (slug(value) or None) if value is not None else None

    @field_validator("peers")
    @classmethod
    def _peer_tickers(cls, value: list[str] | None) -> list[str] | None:
        return _tickers(value)


class DamodaranConfig(_Section):
    enabled: bool = True
    # Professor Damodaran's (NYU Stern) US industry averages, updated every January.
    pe_url: str = Field(
        default="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/pedata.html",
        pattern=r"^https://\S+$",
    )
    ev_ebitda_url: str = Field(
        default="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/vebitda.html",
        pattern=r"^https://\S+$",
    )
    price_to_sales_url: str = Field(
        default="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/psdata.html",
        pattern=r"^https://\S+$",
    )
    # Yahoo industry name -> Damodaran industry name.
    industries: dict[str, str] = {}


class BenchmarksConfig(_Section):
    # The industry's largest companies used as peers (the stock itself left out).
    industry_peers: int = Field(default=10, ge=1, le=30)
    # The sector's largest companies used for the sector benchmark.
    sector_companies: int = Field(default=25, ge=1, le=50)
    # Peer figures and group lists are reused for this many hours.
    refresh_hours: int = Field(default=24, ge=1, le=168)
    tickers: dict[str, TickerBenchmarks] = {}
    damodaran: DamodaranConfig = DamodaranConfig()

    @field_validator("tickers")
    @classmethod
    def _ticker_keys(cls, value: dict[str, TickerBenchmarks]) -> dict[str, TickerBenchmarks]:
        return dict(zip(_tickers(list(value)) or [], value.values(), strict=True))

    def for_ticker(self, ticker: str) -> TickerBenchmarks:
        return self.tickers.get(ticker) or TickerBenchmarks()


def load_benchmarks_config(path: Path) -> BenchmarksConfig:
    """Read config/benchmarks.yaml."""
    return _load_yaml(path, BenchmarksConfig, "Valuation benchmarks")


# --- config/institutional.yaml (funds followed, unusual options, SPEC F4.8) ---------


class FollowedFund(_Section):
    name: str = Field(min_length=1, max_length=100)  # shown in reports
    cik: int = Field(ge=1, le=9_999_999_999)  # the SEC's id number for the fund


class FollowedFundsConfig(_Section):
    """Funds whose quarterly 13F holdings are shown in reports (SPEC F4.8)."""

    enabled: bool = True
    funds: list[FollowedFund] = Field(default=[], max_length=50)
    # Ticker -> CUSIP, for a stock the 13F reports don't name the way Yahoo/the SEC do.
    cusips: dict[str, str] = {}

    @field_validator("cusips")
    @classmethod
    def _cusips(cls, value: dict[str, str]) -> dict[str, str]:
        checked = {}
        for ticker, cusip in value.items():
            if not re.fullmatch(r"[0-9A-Za-z]{9}", str(cusip)):
                raise ValueError(f"'{cusip}' isn't a CUSIP (9 letters and digits)")
            checked[(_tickers([ticker]) or [ticker])[0]] = str(cusip).upper()
        return checked


class UnusualOptionsConfig(_Section):
    """Option contracts traded far more than usual today (SPEC F4.8)."""

    enabled: bool = True
    # A contract is unusual when today's volume is more than this many times its open
    # interest (contracts open at the start of the day)...
    volume_to_open_interest: float = Field(default=2.0, ge=0.5, le=100)
    # ...and at least this many contracts traded.
    min_volume: int = Field(default=500, ge=1)
    # Only expiration dates within this many days, at most this many of them per stock.
    days_ahead: int = Field(default=60, ge=1, le=730)
    max_expirations: int = Field(default=6, ge=1, le=30)
    # Most contracts listed per stock (the biggest volume first).
    max_rows: int = Field(default=10, ge=1, le=50)


class InstitutionalConfig(_Section):
    followed_funds: FollowedFundsConfig = FollowedFundsConfig()
    unusual_options: UnusualOptionsConfig = UnusualOptionsConfig()


def load_institutional_config(path: Path) -> InstitutionalConfig:
    """Read config/institutional.yaml."""
    return _load_yaml(path, InstitutionalConfig, "Institutional activity")


# --- config/x.yaml (X/Twitter posts, SPEC F4.3, DECISIONS 2026-11-05) -----------------

_X_HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


class XConfig(_Section):
    """X posts in reports: trusted accounts first, then a $TICKER search, within a budget."""

    enabled: bool = False
    # Trusted accounts, by handle (with or without the "@").
    accounts: list[str] = Field(default=[], max_length=100)
    # Also search all of X for "$TICKER" with whatever budget the trusted accounts leave.
    ticker_search: bool = True
    # Hard spending limits (US dollars). The app stops asking X once either is reached.
    monthly_budget_usd: float = Field(default=5.0, ge=0, le=1000)
    daily_budget_usd: float = Field(default=0.22, ge=0, le=100)
    # X's pay-per-use prices: each post read, and each account (user) record read.
    price_per_post_usd: float = Field(default=0.005, gt=0, le=1)
    price_per_user_usd: float = Field(default=0.010, gt=0, le=1)
    # Posts asked for per search (X's smallest allowed answer is 10, its largest 100).
    posts_per_search: int = Field(default=10, ge=10, le=100)
    # Search posts naming more "$TICKERS" than this are skipped as likely spam.
    max_cashtags_per_post: int = Field(default=5, ge=1, le=50)

    @field_validator("accounts")
    @classmethod
    def _handles(cls, value: list[str]) -> list[str]:
        handles = []
        for raw in value:
            handle = str(raw).strip().removeprefix("@")
            if not _X_HANDLE.match(handle):
                raise ValueError(f"'{raw}' isn't an X handle (up to 15 letters, digits or _)")
            handles.append(handle)
        return list(dict.fromkeys(h.lower() for h in handles))


def load_x_config(path: Path) -> XConfig:
    """Read config/x.yaml."""
    return _load_yaml(path, XConfig, "X (Twitter)")


# --- config/accounts.yaml (login, SPEC F7.2) -------------------------------------------

# One "@", something on both sides, no spaces. Deliberately loose: "owner@localhost" passes.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+$")
MAX_EMAIL = 320


class ScryptCost(_Section):
    """How much work turning a password into its stored hash takes (Python's scrypt).

    Higher = slower to guess, but each login also takes longer and uses more memory
    (about 128 * n * r bytes: 32 MB with the defaults). The cost is stored with each
    hash, so changing it only affects passwords set afterwards.
    """

    n: int = Field(default=2**15, ge=2, le=2**20)  # must be a power of two
    r: int = Field(default=8, ge=1, le=32)
    p: int = Field(default=1, ge=1, le=16)

    @field_validator("n")
    @classmethod
    def _power_of_two(cls, value: int) -> int:
        if value & (value - 1):
            raise ValueError(f"n must be a power of two (like 16384 or 32768), not {value}")
        return value


class AccountsConfig(_Section):
    scrypt: ScryptCost = ScryptCost()
    # How many days you stay logged in on a device before being asked again.
    session_days: int = Field(default=30, ge=1, le=365)
    # Shortest password allowed when one is set (sign-up, the owner command).
    min_password_length: int = Field(default=12, ge=8, le=128)
    # Ask for a login before showing any page (F7.4). Off on your own computer; on when
    # the app runs online.
    login_required: bool = False
    # Wrong passwords (F7.7): after this many for one email within the window, password
    # logins for that email are refused for `lockout_minutes`. Google sign-in still works.
    max_failed_logins: int = Field(default=10, ge=1, le=1000)
    failed_login_window_minutes: int = Field(default=15, ge=1, le=1440)
    lockout_minutes: int = Field(default=15, ge=1, le=1440)
    # Emails that may create an account (F7.5), compared without case. Empty = nobody.
    invited: list[str] = Field(default=[], max_length=500)

    @field_validator("invited")
    @classmethod
    def _emails(cls, value: list[str]) -> list[str]:
        cleaned = []
        for raw in value:
            email = str(raw).strip().lower()
            if len(email) > MAX_EMAIL or not EMAIL_PATTERN.match(email):
                raise ValueError(f"'{raw}' in the invite list isn't an email address")
            cleaned.append(email)
        return list(dict.fromkeys(cleaned))

    def is_invited(self, email: str) -> bool:
        return email.strip().lower() in self.invited


def load_accounts_config(path: Path) -> AccountsConfig:
    """Read config/accounts.yaml."""
    return _load_yaml(path, AccountsConfig, "Accounts")


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


# --- config/macro.yaml (the macro dashboard, SPEC F6) ---------------------------------

# Where an indicator's numbers come from:
# - fred: a FRED series (any series id from fred.stlouisfed.org), the usual case (F6.3)
# - ici: ICI's weekly money market fund assets
# - equity_risk_premium: S&P 500 earnings yield (1 / the index fund's P/E) minus the
#   10-year Treasury yield, worked out in app/services/macro_math.py
# - largest_forward_pe: forward P/E of the largest US companies together (an estimate of
#   the S&P 500's forward P/E)
MacroSource = Literal["fred", "ici", "equity_risk_premium", "largest_forward_pe"]
# What the stored numbers are: a percentage (4.25 = 4.25%), an index level, a ratio (a P/E),
# or US dollars in millions / billions / trillions.
MacroUnit = Literal["percent", "index", "ratio", "usd_millions", "usd_billions", "usd_trillions"]
_FRED_SERIES = re.compile(r"^[A-Za-z0-9_.]{1,40}$")


def _fred_series_id(value: str) -> str:
    value = str(value).strip().upper()
    if not _FRED_SERIES.match(value):
        raise ValueError(f"'{value}' isn't a FRED series id (letters, digits, _ or .)")
    return value


class MacroIndicator(_Section):
    """One card on the macro page."""

    label: str = Field(min_length=1, max_length=80)
    group: str = Field(default="Other", min_length=1, max_length=40)  # section heading
    source: MacroSource = "fred"
    series: str | None = None  # the FRED series id, e.g. T10Y2Y (FRED indicators only)
    # "value" shows the numbers as they are; "yearly_change" shows the % change from the
    # same date a year earlier (how inflation is quoted, from a price index like CPI).
    show: Literal["value", "yearly_change"] = "value"
    unit: MacroUnit = "percent"
    decimals: int = Field(default=2, ge=0, le=4)
    note: str | None = Field(default=None, max_length=300)  # a short explanation on the card

    @field_validator("series")
    @classmethod
    def _series_id(cls, value: str | None) -> str | None:
        return None if value is None else _fred_series_id(value)

    @model_validator(mode="after")
    def _series_matches_source(self) -> "MacroIndicator":
        if self.source == "fred" and not self.series:
            raise ValueError(f"'{self.label}': a FRED indicator needs its series id")
        if self.source != "fred" and self.series:
            raise ValueError(f"'{self.label}': 'series' is only for FRED indicators")
        return self

    @property
    def display_unit(self) -> MacroUnit:
        return "percent" if self.show == "yearly_change" else self.unit


class IciConfig(_Section):
    # ICI's weekly "Money Market Fund Assets" release page.
    page_url: str = Field(
        default="https://www.ici.org/research/stats/mmf", pattern=r"^https://\S+$"
    )


class EquityRiskPremiumConfig(_Section):
    # The fund whose P/E stands for the S&P 500's, and the Treasury yield taken off it.
    index_fund: str = "SPY"
    treasury_series: str = "DGS10"

    @field_validator("index_fund")
    @classmethod
    def _fund(cls, value: str) -> str:
        return (_tickers([value]) or [value])[0]

    @field_validator("treasury_series")
    @classmethod
    def _treasury(cls, value: str) -> str:
        return _fred_series_id(value)


class LargestForwardPeConfig(_Section):
    # How many of the largest US companies make up the estimate.
    companies: int = Field(default=50, ge=10, le=200)
    # Most Yahoo company requests per refresh (a safety limit; about 65 are usually needed).
    max_requests: int = Field(default=200, ge=20, le=600)


class MacroConfig(_Section):
    # Figures are checked again after this many hours, or this many minutes after a failure.
    refresh_hours: int = Field(default=12, ge=1, le=168)
    retry_minutes: int = Field(default=15, ge=1, le=1440)
    # The longest chart (F6.1: 1 to 5 years).
    history_years: int = Field(default=5, ge=1, le=10)
    ici: IciConfig = IciConfig()
    equity_risk_premium: EquityRiskPremiumConfig = EquityRiskPremiumConfig()
    largest_forward_pe: LargestForwardPeConfig = LargestForwardPeConfig()
    indicators: list[MacroIndicator] = Field(default=[], max_length=60)

    @field_validator("indicators")
    @classmethod
    def _unique_labels(cls, value: list[MacroIndicator]) -> list[MacroIndicator]:
        seen: set[str] = set()
        for indicator in value:
            key = indicator.label.strip().lower()
            if key in seen:
                raise ValueError(f"two indicators are called '{indicator.label}'")
            seen.add(key)
        return value


def load_macro_config(path: Path) -> MacroConfig:
    """Read config/macro.yaml."""
    return _load_yaml(path, MacroConfig, "Macro dashboard")


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
