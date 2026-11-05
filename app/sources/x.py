"""X (Twitter) posts for the research report (SPEC F4.3, DECISIONS 2026-11-05).

Uses X's official pay-per-use API (no scraping): every post X sends us costs money, so:

- Trusted accounts first: during collection, each ticker gets one search for posts from the
  accounts in config/x.yaml that mention it ("from:a OR from:b ... $AMZN OR Amazon").
- Then the $TICKER search, with the budget the trusted accounts left: it runs after every
  ticker of the run is collected (a "late" source), the tickers searched longest ago first.
- Nothing is read twice: each search only asks for posts newer than its previous search
  (`x_checks` table).
- Hard budgets (daily and monthly, US dollars): before each request the most it could cost
  is set aside (`x_spending` table); the request is only sent if that fits both budgets.
  Afterwards the set-aside is reduced to what X actually sent. Counted on the high side:
  X doesn't charge twice for the same post or account on the same UTC day, we do.

Only public post text goes to the AI (F4.13), like news headlines.
"""

import hashlib
import logging
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import Engine
from sqlmodel import Session, func, select

from app.config import XConfig
from app.db.models import XCheck, XSpending
from app.sources.base import SourceError, SourceItem
from app.sources.mentions import company_names
from app.timeutils import as_utc, utc_now

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
POST_URL = "https://x.com/i/web/status/{post_id}"
# Longest search X accepts on the pay-per-use plan (characters); we stay under it.
MAX_QUERY_CHARS = 512
# X's recent search looks back 7 days, and its end time must be a little in the past.
SEARCH_DAYS = 7
END_GAP = timedelta(seconds=30)
TITLE_CHARS = 140
_POST_ID = re.compile(r"^[0-9]{1,25}$")
_CASHTAG_TICKER = re.compile(r"^[A-Z]{1,6}$")
_CASHTAG = re.compile(r"(?<![A-Za-z0-9])\$[A-Za-z]{1,6}(?![A-Za-z0-9])")

_PLACEHOLDER_VALUE = "your-x-bearer-token-here"  # the value in .env.example
NOT_SET_UP = 'not set up: add X_BEARER_TOKEN to .env (README: "Setting up X").'


# --- talking to X ---------------------------------------------------------------------


@dataclass(frozen=True)
class XPost:
    post_id: str
    text: str
    created_at: datetime  # UTC
    username: str | None  # only when authors were asked for
    likes: int = 0
    reposts: int = 0


@dataclass(frozen=True)
class XPage:
    posts: list[XPost]
    users: int  # account records X sent along (each one is charged)


class XBadAnswer(SourceError):
    """X answered, but not in the expected shape. It may have charged us anyway."""


class XApi:
    """X's recent search (GET /2/tweets/search/recent) with the app's Bearer Token."""

    def __init__(
        self,
        bearer_token: str,
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,  # tests only
    ) -> None:
        self.is_set_up = bearer_token.strip() not in ("", _PLACEHOLDER_VALUE)
        self._client = httpx.Client(
            transport=transport,
            headers={"Authorization": f"Bearer {bearer_token.strip()}"},
            timeout=timeout_seconds,
        )

    def search_recent(
        self,
        query: str,
        start: datetime,
        end: datetime,
        max_results: int,
        sort_order: str = "recency",
        with_authors: bool = False,
    ) -> XPage:
        if not self.is_set_up:
            raise SourceError(NOT_SET_UP)
        params = {
            "query": query,
            "start_time": _x_time(start),
            "end_time": _x_time(end),
            "max_results": str(max_results),
            "sort_order": sort_order,
            "tweet.fields": "created_at,public_metrics,note_tweet",
        }
        if with_authors:
            params["expansions"] = "author_id"
            params["user.fields"] = "username"
        try:
            response = self._client.get(SEARCH_URL, params=params)
        except httpx.TimeoutException as exc:
            raise SourceError("X didn't answer in time.") from exc
        except httpx.HTTPError as exc:
            raise SourceError(f"Couldn't reach X ({type(exc).__name__}).") from exc
        if response.status_code != 200:
            raise SourceError(_status_message(response))
        try:
            return parse_search(response.json())
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            log.warning("Unexpected answer from X: %s", exc)
            raise XBadAnswer("X sent an answer we couldn't read.") from exc

    def close(self) -> None:
        self._client.close()


def _x_time(moment: datetime) -> str:
    return as_utc(moment).strftime("%Y-%m-%dT%H:%M:%SZ")


def _status_message(response: httpx.Response) -> str:
    code = response.status_code
    try:
        detail = str(response.json().get("detail") or "")[:200]
    except ValueError:
        detail = ""
    log.warning("X answered %s: %s", code, detail)
    if code == 401:
        return "X refused the key (401): check X_BEARER_TOKEN in .env."
    if code == 402:
        return "X says the prepaid credit is used up (402): add credit in X's developer console."
    if code == 403:
        return f"X refused access (403). {detail}".strip()
    if code == 429:
        return (
            "X says we're asking too often, or the spending limit set in X's developer "
            "console is reached (429)."
        )
    if code == 400:
        return f"X didn't accept the search (400). {detail}".strip()
    return f"X answered with an error ({code})."


def parse_search(answer: Mapping) -> XPage:
    """X's JSON answer -> posts. An answer with no posts has no "data" at all."""
    users = answer.get("includes", {}).get("users", [])
    names = {str(u["id"]): str(u["username"]) for u in users}
    posts = []
    for raw in answer.get("data", []):
        post_id = str(raw["id"])
        if not _POST_ID.match(post_id):
            raise ValueError(f"odd post id {post_id!r}")
        long_text = (raw.get("note_tweet") or {}).get("text")
        metrics = raw.get("public_metrics") or {}
        posts.append(
            XPost(
                post_id=post_id,
                text=str(long_text or raw["text"]),
                created_at=as_utc(datetime.fromisoformat(str(raw["created_at"]))),
                username=names.get(str(raw.get("author_id"))),
                likes=int(metrics.get("like_count", 0)),
                reposts=int(metrics.get("retweet_count", 0)),
            )
        )
    return XPage(posts=posts, users=len(users))


# --- the budget -----------------------------------------------------------------------


@dataclass(frozen=True)
class Reservation:
    day: date
    amount: float


class XLedger:
    """Running X spending (per UTC day) and when each search last ran. Stored in the
    database so the budgets hold across restarts. Safe to use from several threads."""

    def __init__(self, engine: Engine, config: XConfig) -> None:
        self._engine = engine
        self._config = config
        self._lock = threading.Lock()

    def cost(self, posts: int, users: int) -> float:
        return posts * self._config.price_per_post_usd + users * self._config.price_per_user_usd

    def spent(self, now: datetime) -> tuple[float, float]:
        """(today, this month) in US dollars, UTC days."""
        today = as_utc(now).date()
        with Session(self._engine) as session:
            day = session.exec(select(XSpending.cost_usd).where(XSpending.day == today)).first()
            month = session.exec(
                select(func.sum(XSpending.cost_usd)).where(
                    XSpending.day >= today.replace(day=1), XSpending.day <= today
                )
            ).one()
        return float(day or 0.0), float(month or 0.0)

    def reserve(self, worst_case: float, now: datetime) -> Reservation:
        """Set aside the most a request could cost. Raises SourceError if that doesn't fit."""
        cfg = self._config
        with self._lock:
            today_spent, month_spent = self.spent(now)
            if month_spent + worst_case > cfg.monthly_budget_usd + 1e-9:
                raise SourceError(
                    f"skipped: this month's X budget (${cfg.monthly_budget_usd:.2f}) is used up."
                )
            if today_spent + worst_case > cfg.daily_budget_usd + 1e-9:
                raise SourceError(
                    f"skipped: today's X budget (${cfg.daily_budget_usd:.2f}) is used up; "
                    "X is read again tomorrow."
                )
            reservation = Reservation(as_utc(now).date(), worst_case)
            self._add(reservation.day, worst_case, 0, 0)
            return reservation

    def settle(self, reservation: Reservation, posts: int, users: int) -> float:
        """Replace the set-aside with what X actually sent. Returns that cost."""
        actual = min(self.cost(posts, users), reservation.amount)
        with self._lock:
            self._add(reservation.day, actual - reservation.amount, posts, users)
        return actual

    def _add(self, day: date, cost: float, posts: int, users: int) -> None:
        with Session(self._engine) as session:
            row = session.exec(select(XSpending).where(XSpending.day == day)).first()
            if row is None:
                row = XSpending(day=day)
                session.add(row)
            row.cost_usd = round(max(0.0, row.cost_usd + cost), 6)
            row.posts += posts
            row.users += users
            session.commit()

    def summary(self, now: datetime) -> dict:
        today, month = self.spent(now)
        return {
            "today_usd": round(today, 4),
            "daily_budget_usd": self._config.daily_budget_usd,
            "month_usd": round(month, 4),
            "monthly_budget_usd": self._config.monthly_budget_usd,
        }

    # When each search last ran.

    def checked_until(self, ticker: str, kind: str) -> datetime | None:
        with Session(self._engine) as session:
            row = self._check(session, ticker, kind)
            return as_utc(row.checked_until) if row else None

    def mark_checked(self, ticker: str, kind: str, until: datetime, now: datetime) -> None:
        with Session(self._engine) as session:
            row = self._check(session, ticker, kind)
            if row is None:
                row = XCheck(ticker=ticker, kind=kind, checked_until=until, checked_at=now)
                session.add(row)
            row.checked_until, row.checked_at = until, now
            session.commit()

    def least_recently_searched(self, tickers: Sequence[str], kind: str) -> list[str]:
        """Tickers never searched first, then the longest ago; ties keep their order."""
        with Session(self._engine) as session:
            rows = session.exec(
                select(XCheck).where(XCheck.kind == kind, XCheck.ticker.in_(list(tickers)))
            ).all()
        last = {row.ticker: as_utc(row.checked_at) for row in rows}
        oldest = datetime.min.replace(tzinfo=UTC)
        return sorted(tickers, key=lambda t: last.get(t, oldest))

    @staticmethod
    def _check(session: Session, ticker: str, kind: str) -> XCheck | None:
        return session.exec(
            select(XCheck).where(XCheck.ticker == ticker, XCheck.kind == kind)
        ).first()


# --- building searches ----------------------------------------------------------------


def cashtag(ticker: str) -> str | None:
    """'AMZN' -> '$AMZN'. Tickers like 'BRK-B' have no simple cashtag."""
    return f"${ticker}" if _CASHTAG_TICKER.match(ticker) else None


def mention_terms(ticker: str, names: Sequence[str]) -> list[str]:
    """What counts as mentioning the stock: its cashtag, the ticker as a word (3+ letters,
    like mentions.py) and the company's names, each as an exact phrase."""
    terms = []
    if tag := cashtag(ticker):
        terms.append(tag)
    if len(ticker) > 2:
        terms.append(f'"{ticker}"')
    for name in names:
        clean = " ".join(name.replace('"', " ").split())
        if clean:
            terms.append(f'"{clean}"')
    return list(dict.fromkeys(terms))


def account_groups(accounts: Sequence[str], terms: Sequence[str]) -> list[list[str]]:
    """Split the trusted accounts into groups whose searches fit X's length limit."""
    groups: list[list[str]] = []
    current: list[str] = []
    for account in accounts:
        if current and len(accounts_query(current + [account], terms)) > MAX_QUERY_CHARS:
            groups.append(current)
            current = []
        current.append(account)
    if current:
        groups.append(current)
    return groups


def accounts_query(accounts: Sequence[str], terms: Sequence[str]) -> str:
    froms = " OR ".join(f"from:{a}" for a in accounts)
    return f"({froms}) ({' OR '.join(terms)}) -is:retweet"


def ticker_query(tag: str) -> str:
    return f"{tag} -is:retweet -is:reply lang:en"


def group_kind(accounts: Sequence[str]) -> str:
    """A short, stable name for one group of accounts (changes when the group changes)."""
    digest = hashlib.sha256(" ".join(sorted(accounts)).encode()).hexdigest()[:16]
    return f"accounts:{digest}"


def post_title(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= TITLE_CHARS else flat[: TITLE_CHARS - 1].rstrip() + "…"


def post_url(post_id: str) -> str:
    """One address per post, whoever found it, so a post is stored once per ticker."""
    return POST_URL.format(post_id=post_id)


def count_cashtags(text: str) -> int:
    return len(_CASHTAG.findall(text))


# --- the two sources ------------------------------------------------------------------


class _XSearching:
    """What both X sources share: the time window, the budget, and turning posts into items."""

    source_type = "x"

    def __init__(self, api: XApi, ledger: XLedger, config: XConfig) -> None:
        self._api = api
        self._ledger = ledger
        self._config = config

    def _search(
        self,
        ticker: str,
        kind: str,
        query: str,
        since: datetime,
        worst_case: float,
        sort_order: str,
        with_authors: bool,
    ) -> list[XPost]:
        if not self._api.is_set_up:
            raise SourceError(NOT_SET_UP)
        moment = utc_now()
        end = moment - END_GAP
        start = max(as_utc(since), moment - timedelta(days=SEARCH_DAYS) + timedelta(minutes=1))
        last = self._ledger.checked_until(ticker, kind)
        if last is not None:
            start = max(start, last)
        if start >= end - timedelta(seconds=10):
            return []  # searched moments ago
        reservation = self._ledger.reserve(worst_case, moment)
        try:
            page = self._api.search_recent(
                query, start, end, self._config.posts_per_search, sort_order, with_authors
            )
        except XBadAnswer:
            raise  # X may have charged: the set-aside stays counted
        except SourceError:
            self._ledger.settle(reservation, 0, 0)  # X refused: nothing was charged
            raise
        self._ledger.settle(reservation, len(page.posts), page.users)
        self._ledger.mark_checked(ticker, kind, end, moment)
        return page.posts

    def enrich(self, item: SourceItem) -> SourceItem:
        return item

    def _item(self, ticker: str, post: XPost, outlet: str) -> SourceItem:
        return SourceItem(
            source=self.name,
            source_type="x",
            ticker=ticker,
            outlet=outlet,
            title=post_title(post.text) or "(empty post)",
            url=post_url(post.post_id),
            published_at=post.created_at,
            text=post.text,
            data={
                "post_id": post.post_id,
                "username": post.username,
                "likes": post.likes,
                "reposts": post.reposts,
            },
        )


class XAccountsSource(_XSearching):
    """Posts from the trusted accounts that mention the ticker or company."""

    name = "x_accounts"
    label = "X: trusted accounts"

    def __init__(
        self,
        api: XApi,
        ledger: XLedger,
        config: XConfig,
        also_known_as: Mapping[str, Sequence[str]],
    ) -> None:
        super().__init__(api, ledger, config)
        self._also_known_as = also_known_as

    def fetch(self, ticker: str, company: str | None, since: datetime) -> list[SourceItem]:
        accounts = self._config.accounts
        if not accounts:
            return []
        terms = mention_terms(ticker, company_names(company, self._also_known_as.get(ticker, ())))
        items: list[SourceItem] = []
        for group in account_groups(accounts, terms):
            # Each post can come with its author's account record (charged separately).
            worst = self._ledger.cost(
                self._config.posts_per_search, min(self._config.posts_per_search, len(group))
            )
            try:
                posts = self._search(
                    ticker,
                    group_kind(group),
                    accounts_query(group, terms),
                    since,
                    worst,
                    sort_order="recency",
                    with_authors=True,
                )
            except SourceError:
                if not items:
                    raise
                # Keep the posts already paid for; this group is tried again next time.
                log.info("X: a later group of trusted accounts was skipped for %s", ticker)
                break
            for post in posts:
                outlet = f"@{post.username}" if post.username else "X (trusted account)"
                items.append(self._item(ticker, post, outlet))
        return items


class XTickerSearchSource(_XSearching):
    """The $TICKER search across all of X: English, no reposts or replies, most relevant
    first. Authors aren't asked for (that would triple the cost); the link shows them."""

    name = "x_search"
    label = "X: $TICKER search"
    kind = "search"

    def order(self, tickers: Sequence[str]) -> list[str]:
        """Searched longest ago first, so a small budget rotates through the watchlist."""
        return self._ledger.least_recently_searched(tickers, self.kind)

    def fetch(self, ticker: str, company: str | None, since: datetime) -> list[SourceItem]:
        tag = cashtag(ticker)
        if tag is None:
            return []  # e.g. BRK-B: no simple cashtag to search for
        worst = self._ledger.cost(self._config.posts_per_search, 0)
        posts = self._search(
            ticker, self.kind, ticker_query(tag), since, worst, "relevancy", with_authors=False
        )
        limit = self._config.max_cashtags_per_post
        return [
            self._item(ticker, post, f"X ({tag} search)")
            for post in posts
            if count_cashtags(post.text) <= limit
        ]
