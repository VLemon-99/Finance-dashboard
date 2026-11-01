"""Database tables. Times are stored in UTC.

Rule: every user-owned table has a `user_id` column (F7.1). Market data tables
(`instruments`, `quotes`) are shared facts about the market, not owned by a user.
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Column, UniqueConstraint
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
