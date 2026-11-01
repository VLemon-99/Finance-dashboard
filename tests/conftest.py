"""Shared test setup. Tests never touch the network or the real data/app.db."""

import json
from dataclasses import replace
from datetime import date
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
