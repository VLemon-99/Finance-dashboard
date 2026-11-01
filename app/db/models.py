"""Database tables. Times are stored in UTC.

Rule: every user-owned table has a `user_id` column (F7.1). Market data tables
(`instruments`, `quotes`) are shared facts about the market, not owned by a user.
"""

from datetime import datetime

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
