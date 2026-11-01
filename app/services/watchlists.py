"""Watchlists: create, rename, delete, add/remove stocks, and the rows a list page shows.

F1.2: manage lists and items without typing any price data.
F1.3: limits come from config/settings.yaml; over a limit -> clear message, nothing saved.
F1.7: an unknown ticker shows "not found" and is not added.

Every function takes the user's id and only touches that user's lists.
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.config import WatchlistLimits
from app.db.models import Instrument, Watchlist, WatchlistItem
from app.providers.market_data import MarketData
from app.services.quotes import QuoteView, get_quotes, lookup_quote
from app.tickers import InvalidTicker, normalize_ticker

MAX_NAME_LENGTH = 60


class WatchlistError(ValueError):
    """Something the user asked for can't be done. The message is shown on the page."""


class WatchlistNotFound(LookupError):
    """No such list (or it belongs to someone else)."""


@dataclass(frozen=True)
class WatchlistSummary:
    id: int
    name: str
    item_count: int


@dataclass(frozen=True)
class WatchlistRow:
    item_id: int
    quote: QuoteView


def clean_name(raw: str) -> str:
    """Trim and collapse spaces. Rejects empty, too long, or invisible/control characters."""
    name = " ".join((raw or "").split())
    if not name:
        raise WatchlistError("Please give the list a name.")
    if len(name) > MAX_NAME_LENGTH:
        raise WatchlistError(f"List names can be at most {MAX_NAME_LENGTH} characters.")
    if not name.isprintable():
        raise WatchlistError("List names can't contain special control characters.")
    return name


def list_watchlists(session: Session, user_id: int) -> list[WatchlistSummary]:
    statement = (
        select(Watchlist.id, Watchlist.name, func.count(col(WatchlistItem.id)))
        .outerjoin(WatchlistItem, col(WatchlistItem.watchlist_id) == col(Watchlist.id))
        .where(Watchlist.user_id == user_id)
        .group_by(col(Watchlist.id))
        .order_by(col(Watchlist.id))
    )
    return [WatchlistSummary(id=i, name=n, item_count=c) for i, n, c in session.exec(statement)]


def get_watchlist(session: Session, user_id: int, watchlist_id: int) -> Watchlist:
    watchlist = session.get(Watchlist, watchlist_id)
    if watchlist is None or watchlist.user_id != user_id:
        raise WatchlistNotFound(watchlist_id)
    return watchlist


def create_watchlist(
    session: Session, user_id: int, raw_name: str, limits: WatchlistLimits
) -> Watchlist:
    name = clean_name(raw_name)
    count = session.exec(
        select(func.count(col(Watchlist.id))).where(Watchlist.user_id == user_id)
    ).one()
    if count >= limits.max_watchlists:
        raise WatchlistError(
            f"You already have {count} watchlists, the maximum. Delete one first, or raise "
            "max_watchlists in config/settings.yaml."
        )
    _check_name_is_free(session, user_id, name)
    watchlist = Watchlist(user_id=user_id, name=name)
    session.add(watchlist)
    session.commit()
    session.refresh(watchlist)
    return watchlist


def rename_watchlist(session: Session, user_id: int, watchlist_id: int, raw_name: str) -> Watchlist:
    watchlist = get_watchlist(session, user_id, watchlist_id)
    name = clean_name(raw_name)
    if name != watchlist.name:
        _check_name_is_free(session, user_id, name, except_id=watchlist.id)
        watchlist.name = name
        session.add(watchlist)
        session.commit()
        session.refresh(watchlist)
    return watchlist


def delete_watchlist(session: Session, user_id: int, watchlist_id: int) -> None:
    """Deletes the list and its items. Stored prices are kept (they're shared market data)."""
    watchlist = get_watchlist(session, user_id, watchlist_id)
    for item in session.exec(
        select(WatchlistItem).where(WatchlistItem.watchlist_id == watchlist.id)
    ):
        session.delete(item)
    session.delete(watchlist)
    session.commit()


def add_ticker(
    session: Session,
    market_data: MarketData,
    user_id: int,
    watchlist_id: int,
    raw_ticker: str,
    limits: WatchlistLimits,
    max_age: timedelta | None = None,
) -> QuoteView:
    """Check the ticker with the data source, then add it. Returns its quote."""
    watchlist = get_watchlist(session, user_id, watchlist_id)
    try:
        symbol = normalize_ticker(raw_ticker)
    except InvalidTicker:
        raise WatchlistError("Please enter a valid US ticker, e.g. AAPL or BRK-B.") from None

    items = _items(session, watchlist.id)
    if any(symbol == s for _, s in items):
        raise WatchlistError(f"{symbol} is already in this list.")
    if len(items) >= limits.max_items_per_list:
        # Checked before asking the data source: nothing is fetched or saved.
        raise WatchlistError(
            f"This list already has {len(items)} items, the maximum. Remove one first, or raise "
            "max_items_per_list in config/settings.yaml."
        )

    quote = lookup_quote(session, market_data, symbol, max_age=max_age)
    if quote.status == "not_found":
        raise WatchlistError(f"Ticker {symbol} not found, so it wasn't added.")
    if quote.status == "error":
        raise WatchlistError(
            f"Couldn't check {symbol} with the data source just now, so it wasn't added. "
            "Please try again in a few minutes."
        )

    instrument = session.exec(select(Instrument).where(Instrument.symbol == symbol)).one()
    session.add(
        WatchlistItem(user_id=user_id, watchlist_id=watchlist.id, instrument_id=instrument.id)
    )
    session.commit()
    return quote


def remove_item(session: Session, user_id: int, watchlist_id: int, item_id: int) -> None:
    watchlist = get_watchlist(session, user_id, watchlist_id)
    item = session.get(WatchlistItem, item_id)
    if item is None or item.watchlist_id != watchlist.id or item.user_id != user_id:
        return  # already gone: nothing to do
    session.delete(item)
    session.commit()


def watchlist_rows(
    session: Session,
    market_data: MarketData,
    user_id: int,
    watchlist_id: int,
    max_age: timedelta | None,
    max_parallel: int = 1,
) -> list[WatchlistRow]:
    """One row per stock, in the order they were added. `max_age=None` forces a refresh."""
    watchlist = get_watchlist(session, user_id, watchlist_id)
    items = _items(session, watchlist.id)
    quotes = get_quotes(
        session, market_data, [s for _, s in items], max_age=max_age, max_parallel=max_parallel
    )
    return [WatchlistRow(item_id=item_id, quote=quotes[symbol]) for item_id, symbol in items]


def _items(session: Session, watchlist_id: int) -> list[tuple[int, str]]:
    """(item id, ticker) pairs in the order they were added."""
    statement = (
        select(WatchlistItem.id, Instrument.symbol)
        .join(Instrument, col(Instrument.id) == col(WatchlistItem.instrument_id))
        .where(WatchlistItem.watchlist_id == watchlist_id)
        .order_by(col(WatchlistItem.id))
    )
    return list(session.exec(statement))


def _check_name_is_free(
    session: Session, user_id: int, name: str, except_id: int | None = None
) -> None:
    """Names are unique per user, ignoring upper/lower case."""
    statement = select(Watchlist.id).where(
        Watchlist.user_id == user_id, func.lower(Watchlist.name) == name.lower()
    )
    if except_id is not None:
        statement = statement.where(Watchlist.id != except_id)
    if session.exec(statement).first() is not None:
        raise WatchlistError(f'You already have a list called "{name}".')
