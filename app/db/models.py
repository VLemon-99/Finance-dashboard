"""Database tables. Times are stored in UTC.

Rule: every user-owned table has a `user_id` column (F7.1). Market data tables
(`instruments`, `quotes`) are shared facts about the market, not owned by a user.
"""

from datetime import date, datetime

from sqlalchemy import JSON, BigInteger, Column, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.timeutils import utc_now


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True, max_length=320)
    created_at: datetime = Field(default_factory=utc_now)


class Instrument(SQLModel, table=True):
    """One stock/ETF (later also option contracts)."""

    __tablename__ = "instruments"

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(default="stock", max_length=10)  # "stock" | "option" (Phase 4)
    symbol: str = Field(unique=True, index=True, max_length=32)


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
