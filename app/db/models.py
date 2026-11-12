"""Database tables. Times are stored in UTC.

Rule: every user-owned table has a `user_id` column (F7.1). Market data tables
(`instruments`, `quotes`) are shared facts about the market, not owned by a user.
"""

from datetime import date, datetime

from sqlalchemy import JSON, BigInteger, Column, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.timeutils import utc_now


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True, max_length=320)
    # Salted scrypt hash (app/services/users.py), never the password. None = can't log in.
    password_hash: str | None = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=utc_now)


class LoginSession(SQLModel, table=True):
    """One signed-in device (F7.2). Only a hash of its token is kept, never the token."""

    __tablename__ = "login_sessions"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(unique=True, index=True, max_length=64)  # SHA-256, hex
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime


class Instrument(SQLModel, table=True):
    """One stock/ETF, or one option contract (Phase 4)."""

    __tablename__ = "instruments"

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(default="stock", max_length=10)  # "stock" | "option"
    symbol: str = Field(unique=True, index=True, max_length=32)  # option: AAPL261120C00260000
    # Option contracts only (empty for stocks):
    underlying: str | None = Field(default=None, max_length=32)  # the stock, e.g. AAPL
    option_type: str | None = Field(default=None, max_length=4)  # "call" | "put"
    strike: float | None = None
    expiration: date | None = None


class Quote(SQLModel, table=True):
    """Latest stored market data for an instrument, with when it was fetched."""

    __tablename__ = "quotes"

    id: int | None = Field(default=None, primary_key=True)
    instrument_id: int = Field(foreign_key="instruments.id", unique=True, index=True)
    last_price: float
    previous_close: float | None = None
    currency: str | None = Field(default=None, max_length=8)
    fetched_at: datetime
    source: str = Field(max_length=32)
    # The rest of the stock row (F1.4). Empty if the source didn't give it.
    session_date: date | None = None  # trading day the price belongs to
    day_low: float | None = None
    day_high: float | None = None
    year_low: float | None = None
    year_high: float | None = None
    volume: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    market_cap: float | None = None
    next_earnings: date | None = None


class OptionQuote(SQLModel, table=True):
    """Latest stored prices for one option contract, with when they were fetched.

    Shared market data like `quotes` (no `user_id`). Every strike of a fetched option chain
    is stored, so the chain page and watchlists can reuse recent prices and fall back on
    them when the source fails (SPEC F1.5, F1.6). One row per contract, overwritten.
    """

    __tablename__ = "option_quotes"
    __table_args__ = (Index("ix_option_quotes_chain", "underlying", "expiration", "fetched_at"),)

    id: int | None = Field(default=None, primary_key=True)
    contract_symbol: str = Field(unique=True, index=True, max_length=32)  # AAPL261120C00260000
    underlying: str = Field(max_length=32)
    expiration: date
    option_type: str = Field(max_length=4)  # "call" | "put"
    strike: float
    bid: float | None = None
    ask: float | None = None
    last_price: float | None = None
    volume: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    open_interest: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    implied_volatility: float | None = None  # a fraction: 0.25 = 25%
    underlying_price: float | None = None  # stock price sent with the chain
    fetched_at: datetime
    source: str = Field(max_length=32)


class Watchlist(SQLModel, table=True):
    """A named list of instruments, owned by a user (F1.2)."""

    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_watchlists_user_name"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    name: str = Field(max_length=60)
    created_at: datetime = Field(default_factory=utc_now)


class WatchlistItem(SQLModel, table=True):
    """One instrument in one watchlist. The same stock can be in several lists."""

    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint(
            "watchlist_id", "instrument_id", name="uq_watchlist_items_list_instrument"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    watchlist_id: int = Field(foreign_key="watchlists.id", index=True)
    instrument_id: int = Field(foreign_key="instruments.id", index=True)
    added_at: datetime = Field(default_factory=utc_now)


class Transaction(SQLModel, table=True):
    """One Bought or Sold trade in the virtual portfolio (SPEC F3.1).

    Only trades are stored; positions, P&L and returns are calculated from them when shown
    (see DECISIONS.md, "Positions are calculated from trades, not stored").
    """

    __tablename__ = "transactions"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    instrument_id: int = Field(foreign_key="instruments.id", index=True)
    side: str = Field(max_length=6)  # "bought" | "sold"
    quantity: int  # shares, or option contracts
    price: float  # per share (options: per share, as quoted; x 100 per contract)
    trade_date: date = Field(index=True)
    price_source: str = Field(max_length=10)  # "auto" (filled in by the app) | "manual"
    # Options only: the stock price when the trade was made (covered-call capital, F3.5).
    underlying_price: float | None = None
    # "trade", or how an option position ended (F3.9): "expired" | "assigned" | "exercised"
    reason: str = Field(default="trade", max_length=10)
    created_at: datetime = Field(default_factory=utc_now)


class Spread(SQLModel, table=True):
    """Two option positions the user linked as one credit spread (SPEC F3.7).

    Links instruments, not trades: while both legs are open with the same number of
    contracts, the portfolio shows and calculates them as one position.
    """

    __tablename__ = "spreads"
    __table_args__ = (
        UniqueConstraint("user_id", "short_instrument_id", name="uq_spreads_user_short"),
        UniqueConstraint("user_id", "long_instrument_id", name="uq_spreads_user_long"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    short_instrument_id: int = Field(foreign_key="instruments.id")  # the leg sold
    long_instrument_id: int = Field(foreign_key="instruments.id")  # the leg bought
    created_at: datetime = Field(default_factory=utc_now)


class SourceItemRow(SQLModel, table=True):
    """One piece of public research content about one ticker (an article, a filing...).

    Public content, so no `user_id` (like `quotes`). Stored once per ticker and URL.
    Near-identical headlines are grouped into one "story" (SPEC F4.12): every item points
    at the story's first item through `story_id` (the first item points at itself).
    """

    __tablename__ = "source_items"
    __table_args__ = (UniqueConstraint("ticker", "url", name="uq_source_items_ticker_url"),)

    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(max_length=32, index=True)
    source: str = Field(max_length=32)  # plug-in name, e.g. "google_news"
    source_type: str = Field(max_length=16)  # "news" | "filing" (later blog, youtube...)
    outlet: str = Field(max_length=200)
    title: str = Field(max_length=1000)
    url: str = Field(max_length=2000)
    published_at: datetime = Field(index=True)
    form: str | None = Field(default=None, max_length=20)  # SEC form type
    text: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    data: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    fetched_at: datetime = Field(default_factory=utc_now)
    story_id: int | None = Field(default=None, foreign_key="source_items.id", index=True)
    # Filled in by the AI step (Gemini). Empty until then.
    summary: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    sentiment: str | None = Field(default=None, max_length=10)  # bullish | neutral | bearish
    sentiment_reason: str | None = Field(default=None, max_length=500)
    is_rumor: bool | None = None  # unconfirmed report / speculation
    summarized_at: datetime | None = None
    ai_model: str | None = Field(default=None, max_length=60)


class ReportRun(SQLModel, table=True):
    """One "Generate now" (or, later, scheduled) run: one ticker or a whole watchlist.

    Holds the progress while it runs and the watchlist summary shown on top when done.
    """

    __tablename__ = "report_runs"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    # The watchlist it was run for (None for a single ticker). No database link, so deleting
    # the watchlist keeps its past reports; `label` keeps the name as it was.
    watchlist_id: int | None = None
    label: str = Field(max_length=100)
    started_at: datetime = Field(default_factory=utc_now, index=True)
    finished_at: datetime | None = None
    status: str = Field(default="running", max_length=10)  # running | done | failed
    total: int = 0  # tickers to do
    done: int = 0  # tickers finished
    summary: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    error: str | None = Field(default=None, max_length=500)


class Report(SQLModel, table=True):
    """One ticker's research report (SPEC F4.1). The content is stored as structured JSON."""

    __tablename__ = "reports"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    run_id: int = Field(foreign_key="report_runs.id", index=True)
    ticker: str = Field(max_length=32, index=True)
    generated_at: datetime = Field(default_factory=utc_now, index=True)
    period_start: datetime  # the date range it covers (F4.14)
    period_end: datetime
    body: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    missing: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))


class CalendarEvent(SQLModel, table=True):
    """One fetched calendar event (SPEC F5): an earnings date or an economic release.

    Public market facts, so no `user_id` (like `quotes`). Fed meetings aren't stored: they
    come straight from config/fomc_dates.yaml. Each refresh replaces all events of its
    `source_key`, because dates can move.
    """

    __tablename__ = "calendar_events"

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(max_length=10)  # "earnings" | "macro"
    source_key: str = Field(max_length=40, index=True)  # "earnings:AAPL", "fred:10"
    ticker: str | None = Field(default=None, max_length=32, index=True)  # earnings only
    title: str = Field(max_length=200)
    event_date: date = Field(index=True)  # the US Eastern calendar day
    starts_at: datetime | None = None  # UTC; None when the time isn't known
    timing: str | None = Field(default=None, max_length=12)  # earnings: before_open...
    source: str = Field(max_length=60)  # shown on the page, e.g. "Yahoo Finance"
    source_url: str | None = Field(default=None, max_length=500)
    fetched_at: datetime = Field(default_factory=utc_now)


class CalendarFetch(SQLModel, table=True):
    """When each calendar source was last checked, and whether it worked (stale/missing)."""

    __tablename__ = "calendar_fetches"

    id: int | None = Field(default=None, primary_key=True)
    source_key: str = Field(max_length=40, unique=True)  # "earnings:AAPL", "fred:10"
    fetched_at: datetime
    ok: bool = True
    message: str | None = Field(default=None, max_length=300)  # why it failed


class XSpending(SQLModel, table=True):
    """What the app has spent on X's pay-per-use API, per UTC day (SPEC F4.3).

    The app's own running total, used to stop before the daily and monthly budgets in
    config/x.yaml are reached. App-wide (one X key for the app), so no `user_id`, like
    `calendar_fetches`.
    """

    __tablename__ = "x_spending"

    id: int | None = Field(default=None, primary_key=True)
    day: date = Field(unique=True, index=True)  # UTC day (X counts its days in UTC)
    posts: int = 0  # posts X sent us
    users: int = 0  # account records X sent us
    cost_usd: float = 0.0  # counted on the high side (see app/sources/x.py)


class XCheck(SQLModel, table=True):
    """Up to when X was last searched, per ticker and search, so no post is paid for twice.

    `kind` is "search" (the $TICKER search) or "accounts:<id>" (one group of trusted
    accounts). Public facts about searches, so no `user_id`.
    """

    __tablename__ = "x_checks"
    __table_args__ = (UniqueConstraint("ticker", "kind", name="uq_x_checks_ticker_kind"),)

    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(max_length=32, index=True)
    kind: str = Field(max_length=40)
    checked_until: datetime  # posts up to this time (UTC) have been read
    checked_at: datetime  # when that search was made


class MacroObservation(SQLModel, table=True):
    """One dated value of a macro dashboard series (SPEC F6): a FRED value, ICI's weekly
    total, or a reading the app saved itself (an index fund's P/E, the largest companies'
    forward P/E). Public market facts, so no `user_id`.

    FRED series are replaced in full on each refresh (FRED revises past values); the
    app's own readings and ICI's weeks are added one date at a time.
    """

    __tablename__ = "macro_observations"
    __table_args__ = (
        UniqueConstraint("series_key", "obs_date", name="uq_macro_observations_series_date"),
    )

    id: int | None = Field(default=None, primary_key=True)
    # "fred:T10Y2Y", "ici:mmf_total", "yahoo:SPY:trailing_pe", "calc:largest_forward_pe"
    series_key: str = Field(max_length=60, index=True)
    obs_date: date
    value: float


class MacroFetch(SQLModel, table=True):
    """When each macro series was last checked, whether it worked, and what the source said
    (e.g. how many companies the forward P/E is based on)."""

    __tablename__ = "macro_fetches"

    id: int | None = Field(default=None, primary_key=True)
    series_key: str = Field(max_length=60, unique=True)
    fetched_at: datetime
    ok: bool = True
    message: str | None = Field(default=None, max_length=300)  # why it failed
    detail: str | None = Field(default=None, max_length=300)  # extra facts from a success
