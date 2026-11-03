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

from app.ai.llm import AIError, Claim, ItemSummary, PublicItem, PublicStory, ReportDraft
from app.config import PROJECT_ROOT, AppConfig
from app.db.session import make_engine
from app.main import create_app
from app.providers.market_data import (
    MarketDataError,
    NoOptionChain,
    OptionChainData,
    QuoteData,
    SearchResult,
    TickerNotFound,
)
from app.providers.yahoo import (
    chain_from_yahoo,
    expirations_from_yahoo,
    quote_from_history,
    search_results_from_quotes,
)
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


def load_yahoo_options(name: str = "yahoo_options_AAPL.json") -> dict:
    """The recorded option sample: 'expirations' (strings), 'calls'/'puts' as the
    DataFrames yfinance returns, and the 'underlying' quote dict."""
    raw = json.loads((FIXTURES / name).read_text())
    for side in ("calls", "puts"):
        raw[side] = pd.DataFrame(raw[side])
    return raw


# Known answers for the option sample.
FAKE_RISK_FREE_RATE = 0.043
OPTION_EXPIRATION = date(2026, 11, 20)


def fake_contract_symbol(symbol: str, expiration: date, option_type: str, strike: float) -> str:
    """OCC-style symbol, e.g. AAPL261120C00260000."""
    return f"{symbol}{expiration:%y%m%d}{option_type[0].upper()}{round(strike * 1000):08d}"


# Pretend "now" for option tests: Tuesday Nov 3, 2026, 10:00 AM Eastern.
OPTIONS_NOW = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)


class Clock:
    """A clock tests can move forward: `clock.now += timedelta(minutes=16)`."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def options_clock(monkeypatch):
    """Freeze 'now' for option fetching, storing and expiry."""
    import app.providers.yahoo
    import app.services.options

    clock = Clock(OPTIONS_NOW)
    for module in (app.providers.yahoo, app.services.options):
        monkeypatch.setattr(module, "utc_now", clock)
    return clock


class FakeMarketData:
    """Stands in for Yahoo. Uses the recorded sample, or simulates failures."""

    def __init__(self) -> None:
        self.mode = "ok"  # "ok" | "down" | "not_found" | "crash"  (applies to every ticker)
        self.not_found: set[str] = set()  # tickers that don't exist, whatever the mode
        self.down_for: set[str] = set()  # tickers whose fetch fails, whatever the mode
        self.calls: list[str] = []
        self.searches: list[str] = []
        # Options (Phase 4)
        self.no_options: set[str] = set()  # tickers without listed options
        self.rate_down = False  # the T-bill rate fetch fails
        self.chain_calls: list[tuple[str, date]] = []
        self.expiration_calls: list[str] = []
        self.rate_calls = 0

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

    def _check_options(self, symbol: str) -> None:
        if self.mode == "down" or symbol in self.down_for:
            raise MarketDataError("Yahoo Finance did not respond properly (ConnectionError).")
        if self.mode == "crash":
            raise RuntimeError("something unexpected")

    def get_option_expirations(self, symbol: str) -> list[date]:
        self.expiration_calls.append(symbol)
        self._check_options(symbol)
        if self.mode == "not_found" or symbol in self.not_found | self.no_options:
            return []
        return expirations_from_yahoo(load_yahoo_options()["expirations"])

    def get_option_chain(self, symbol: str, expiration: date) -> OptionChainData:
        """The sample chain, re-labelled for the ticker and date asked for."""
        self.chain_calls.append((symbol, expiration))
        self._check_options(symbol)
        sample = load_yahoo_options()
        if (
            self.mode == "not_found"
            or symbol in self.not_found | self.no_options
            or expiration.isoformat() not in sample["expirations"]
        ):
            raise NoOptionChain(f"No {symbol} options expiring {expiration}.")
        for side, option_type in (("calls", "call"), ("puts", "put")):
            sample[side]["contractSymbol"] = [
                fake_contract_symbol(symbol, expiration, option_type, k)
                for k in sample[side]["strike"]
            ]
        return chain_from_yahoo(
            symbol, expiration, sample["calls"], sample["puts"], sample["underlying"]
        )

    def get_risk_free_rate(self) -> float:
        self.rate_calls += 1
        if self.rate_down or self.mode in ("down", "crash"):
            raise MarketDataError("Yahoo Finance did not respond properly (Timeout).")
        return FAKE_RISK_FREE_RATE


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
    "https://www.sec.gov/Archives/edgar/data/320193/000032019326000099/aapl-20260927.htm": (
        "sec_10k.htm"
    ),
    "https://www.sec.gov/Archives/edgar/data/320193/000123456726000202/xslSCHEDULE_13G": (
        "sec_13g.htm"
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
    import app.services.reports
    import app.sources.edgar
    import app.sources.news_rss

    for module in (app.sources.edgar, app.sources.news_rss, app.services.reports):
        monkeypatch.setattr(module, "utc_now", lambda: SOURCES_NOW)
    return SOURCES_NOW


# --- the AI (Phase 3): always faked in tests ------------------------------------------


class FakeLLM:
    """Stands in for Gemini. Rates items by simple keyword rules so answers are predictable."""

    model_name = "fake-model"

    def __init__(self) -> None:
        self.fail: str | None = None  # set to a message to make every call raise AIError
        self.skip_refs: set[int] = set()  # items it pretends aren't about the ticker
        self.extra_claims: list[Claim] = []  # added to developments (e.g. uncited ones)
        self.item_calls: list[tuple[str, str | None, list[PublicItem]]] = []
        self.report_calls: list[tuple[str, str | None, list[PublicStory]]] = []

    def summarize_items(self, ticker, company, items):
        if self.fail:
            raise AIError(self.fail)
        self.item_calls.append((ticker, company, list(items)))
        answers = []
        for item in items:
            if item.ref in self.skip_refs:
                continue
            title = item.title.lower()
            if any(w in title for w in ("record", "beat", "buy")):
                sentiment = "bullish"
            elif any(w in title for w in ("slip", "worr", "sell", "recall")):
                sentiment = "bearish"
            else:
                sentiment = "neutral"
            answers.append(
                ItemSummary(
                    ref=item.ref,
                    summary=f"Summary of: {item.title}",
                    sentiment=sentiment,
                    reason=f"Reason for {sentiment}.",
                    is_rumor=any(w in title for w in ("said to", "report:", "rumor")),
                )
            )
        return answers

    def write_report(self, ticker, company, stories, max_developments, max_rumors):
        if self.fail:
            raise AIError(self.fail)
        self.report_calls.append((ticker, company, list(stories)))
        developments = [
            Claim(f"Development: {s.title}.", (s.ref,)) for s in stories if not s.is_rumor
        ]
        rumors = [Claim(f"Rumor: {s.title}.", (s.ref,)) for s in stories if s.is_rumor]
        return ReportDraft(
            developments=tuple(developments[:max_developments] + self.extra_claims),
            rumors=tuple(rumors[:max_rumors]),
        )


@pytest.fixture
def fake_llm():
    return FakeLLM()


# --- the whole app ------------------------------------------------------------------

TEST_SEC_USER_AGENT = "Test Person test.person@mail.test"


@pytest.fixture
def report_services(fake_fetch, fake_llm, sources_now, app_config):
    """Report pipeline wired to the fake internet and the fake AI."""
    from app.config import SourcesConfig
    from app.services.reports import ReportServices
    from app.sources.edgar import EdgarSource
    from app.sources.news_rss import GoogleNewsSource, YahooNewsSource
    from app.sources.registry import SourceSet

    sources_config = SourcesConfig()
    edgar = EdgarSource(fake_fetch, sources_config.sec_edgar, TEST_SEC_USER_AGENT)
    news = sources_config.news
    return ReportServices(
        sources=SourceSet(
            [GoogleNewsSource(fake_fetch, news), YahooNewsSource(fake_fetch, news), edgar], edgar
        ),
        llm=fake_llm,
        llm_problem=None,
        config=app_config,
        sources_config=sources_config,
    )


class RecordingRunner:
    """Runs reports straight away (no background thread), or holds them when `hold` is set."""

    def __init__(self) -> None:
        self.hold = False
        self.started: list[tuple[int, list[str]]] = []

    def __call__(self, engine, run_id, tickers, services):
        from app.services.reports import execute_run

        self.started.append((run_id, list(tickers)))
        if not self.hold:
            execute_run(engine, run_id, tickers, services)


@pytest.fixture
def report_runner():
    return RecordingRunner()


@pytest.fixture
def client(engine, fake_market_data, app_config, report_services, report_runner):
    app = create_app(
        engine=engine,
        market_data=fake_market_data,
        app_config=app_config,
        report_services=report_services,
        report_runner=report_runner,
    )
    with TestClient(app) as c:  # `with` runs startup (creates the default user)
        yield c
