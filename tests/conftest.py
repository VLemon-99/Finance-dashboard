"""Shared test setup. Tests never touch the network or the real data/app.db."""

import json
from pathlib import Path

import pandas as pd
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import PROJECT_ROOT
from app.db.session import make_engine
from app.main import create_app
from app.providers.market_data import MarketDataError, QuoteData, TickerNotFound
from app.providers.yahoo import quote_from_history

FIXTURES = Path(__file__).parent / "fixtures"


def load_yahoo_history(name: str = "yahoo_history_AAPL_5d.json") -> tuple[pd.DataFrame, str]:
    """Load a recorded sample as the DataFrame yfinance would return."""
    raw = json.loads((FIXTURES / name).read_text())
    df = pd.DataFrame(raw["rows"])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("Date"), utc=True)).tz_convert(
        "America/New_York"
    )
    return df, raw["currency"]


class FakeMarketData:
    """Stands in for Yahoo. Uses the recorded sample, or simulates failures."""

    def __init__(self) -> None:
        self.mode = "ok"  # "ok" | "down" | "not_found" | "crash"
        self.calls: list[str] = []

    def get_quote(self, symbol: str) -> QuoteData:
        self.calls.append(symbol)
        if self.mode == "down":
            raise MarketDataError("Yahoo Finance did not respond properly (ConnectionError).")
        if self.mode == "not_found":
            raise TickerNotFound(symbol)
        if self.mode == "crash":
            raise RuntimeError("something unexpected")
        history, currency = load_yahoo_history()
        return quote_from_history(symbol, history, currency)


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
def client(engine, fake_market_data):
    app = create_app(engine=engine, market_data=fake_market_data)
    with TestClient(app) as c:  # `with` runs startup (creates the default user)
        yield c
