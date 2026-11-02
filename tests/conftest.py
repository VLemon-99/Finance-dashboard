"""Shared test setup. Tests never touch the network or the real data/app.db."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import PROJECT_ROOT, AppConfig
from app.db.session import make_engine
from app.main import create_app
from app.providers.market_data import MarketDataError, QuoteData, SearchResult, TickerNotFound
from app.providers.yahoo import quote_from_history, search_results_from_quotes
from app.sources.base import SourceError

FIXTURES = Path(__file__).parent / "fixtures"


# Known answers for the invented sample (see the _note in the fixture file).
FAKE_MARKET_CAP = 3_850_000_000_000.0
FAKE_NEXT_EARNINGS = date(2026, 11, 19)


def load_yahoo_history(name: str = "yahoo_history_AAPL_1y.json") -> tuple[pd.DataFrame, str]:
    """Load a recorded sample as the DataFrame yfinance would return."""
    raw = json.loads((FIXTURES / name).read_text())
    df = pd.DataFrame(raw["rows"])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("Date"), utc=True)).tz_convert(
        "America/New_York"
    )
    return df, raw["currency"]


def load_yahoo_search(name: str = "yahoo_search_amazon.json") -> list[dict]:
    return json.loads((FIXTURES / name).read_text())["quotes"]


class FakeMarketData:
    """Stands in for Yahoo. Uses the recorded sample, or simulates failures."""

    def __init__(self) -> None:
        self.mode = "ok"  # "ok" | "down" | "not_found" | "crash"  (applies to every ticker)
        self.not_found: set[str] = set()  # tickers that don't exist, whatever the mode
        self.down_for: set[str] = set()  # tickers whose fetch fails, whatever the mode
        self.calls: list[str] = []
        self.searches: list[str] = []

    def get_quote(self, symbol: str) -> QuoteData:
        self.calls.append(symbol)
        if self.mode == "down" or symbol in self.down_for:
            raise MarketDataError("Yahoo Finance did not respond properly (ConnectionError).")
        if self.mode == "not_found" or symbol in self.not_found:
            raise TickerNotFound(symbol)
        if self.mode == "crash":
            raise RuntimeError("something unexpected")
        history, currency = load_yahoo_history()
        quote = quote_from_history(symbol, history, currency)
        return replace(quote, market_cap=FAKE_MARKET_CAP, next_earnings=FAKE_NEXT_EARNINGS)

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        self.searches.append(query)
        if self.mode in ("down", "crash"):
            raise MarketDataError("Yahoo Finance search did not respond properly (Timeout).")
        return search_results_from_quotes(load_yahoo_search(), max_results)


@pytest.fixture
def engine(tmp_path):
    """A fresh temporary database, built by running the real migrations."""
    url = f"sqlite:///{tmp_path / 'test.db'}"
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    eng = make_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def fake_market_data():
    return FakeMarketData()


@pytest.fixture
def app_config():
    """Small limits so limit tests stay quick. Tests may replace this fixture's value."""
    return AppConfig.model_validate({"watchlists": {"max_watchlists": 3, "max_items_per_list": 2}})


@pytest.fixture
def client(engine, fake_market_data, app_config):
    app = create_app(engine=engine, market_data=fake_market_data, app_config=app_config)
    with TestClient(app) as c:  # `with` runs startup (creates the default user)
        yield c


# --- research sources (Phase 3) ------------------------------------------------------

SOURCE_FIXTURES = FIXTURES / "sources"

# Pretend "now" for source tests: the samples are dated just before this.
SOURCES_NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

# Address (or the start of one) -> sample file. Checked in order; first match wins.
DEFAULT_ROUTES = {
    "https://news.google.com/rss/search": "google_news_aapl.xml",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL": "yahoo_news_aapl.xml",
    "https://www.sec.gov/files/company_tickers.json": "sec_company_tickers.json",
    "https://data.sec.gov/submissions/CIK0000320193.json": "sec_submissions_aapl.json",
    "https://www.sec.gov/Archives/edgar/data/320193/000032019326000101/wk-form4_1730412312.xml": (
        "sec_form4_sell.xml"
    ),
    "https://www.sec.gov/Archives/edgar/data/320193/000032019326000090/wk-form4_1726423200.xml": (
        "sec_form4_buy.xml"
    ),
    "https://www.sec.gov/Archives/edgar/data/320193/000032019326000100/index.json": (
        "sec_8k_index.json"
    ),
    "https://www.sec.gov/Archives/edgar/data/320193/000032019326000100/aapl-20261030.htm": (
        "sec_8k_primary.htm"
    ),
    "https://www.sec.gov/Archives/edgar/data/320193/000032019326000100/a8-kex991": (
        "sec_8k_ex99.htm"
    ),
}


class FakeFetch:
    """Stands in for the internet: answers known addresses with recorded samples."""

    def __init__(self, routes: dict[str, str] | None = None) -> None:
        self.routes = dict(DEFAULT_ROUTES if routes is None else routes)
        self.down: set[str] = set()  # address starts that fail with SourceError
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if any(url.startswith(prefix) for prefix in self.down):
            raise SourceError("The site didn't answer in time.")
        for prefix, name in self.routes.items():
            if url.startswith(prefix):
                return (SOURCE_FIXTURES / name).read_bytes()
        raise SourceError("The site says this page doesn't exist (404).")


@pytest.fixture
def fake_fetch():
    return FakeFetch()


@pytest.fixture
def sources_now(monkeypatch):
    """Freeze 'now' for the source plug-ins so the sample dates fall in the window."""
    import app.sources.edgar
    import app.sources.news_rss

    for module in (app.sources.edgar, app.sources.news_rss):
        monkeypatch.setattr(module, "utc_now", lambda: SOURCES_NOW)
    return SOURCES_NOW
