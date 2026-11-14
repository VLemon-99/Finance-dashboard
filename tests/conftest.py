"""Shared test setup. Tests never touch the network or the real data/app.db."""

import json
import threading
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.ai.llm import (
    AIError,
    Claim,
    ItemSummary,
    PublicItem,
    PublicStory,
    PublicVideo,
    ReportDraft,
    VideoPoint,
    VideoUnavailable,
)
from app.config import PROJECT_ROOT, AppConfig
from app.db.session import make_engine
from app.main import create_app
from app.providers.market_data import (
    EarningsData,
    MarketDataError,
    NoOptionChain,
    OptionChainData,
    QuoteData,
    SearchResult,
    TickerNotFound,
)
from app.providers.yahoo import (
    annual_figures_from_yahoo,
    chain_from_yahoo,
    close_on,
    expirations_from_yahoo,
    group_from_yahoo,
    ownership_from_yahoo,
    profile_from_info,
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


def load_yahoo_research(name: str = "yahoo_research.json") -> dict:
    """The Phase 7 sample (valuation, sector/industry, holders) as yfinance returns it:
    'info' dicts, 'groups' -> (name, top-companies DataFrame), 'income'/'balance'
    DataFrames (rows = items, columns = year ends), 'history' daily closes,
    'institutions'/'breakdown' holders DataFrames."""
    raw = json.loads((FIXTURES / name).read_text())
    groups = {
        key: (
            g["name"],
            pd.DataFrame(g["top"], columns=["symbol", "name", "rating", "market weight"]).set_index(
                "symbol"
            ),
        )
        for key, g in raw["groups"].items()
    }

    def statement(rows: dict) -> pd.DataFrame:
        df = pd.DataFrame(rows).T
        df.columns = pd.to_datetime(df.columns)
        return df[sorted(df.columns, reverse=True)]  # yfinance: newest year first

    fin = raw["financials"]
    history = pd.DataFrame({"Close": list(fin["closes"].values())})
    history.index = pd.DatetimeIndex(pd.to_datetime(list(fin["closes"]))).tz_localize(
        "America/New_York"
    )
    institutions = pd.DataFrame(raw["holders"]["institutional_holders"])
    institutions["reportDate"] = pd.to_datetime(institutions["reportDate"], unit="s")
    institutions = institutions.rename(
        columns={
            "reportDate": "Date Reported",
            "organization": "Holder",
            "position": "Shares",
            "value": "Value",
        }
    )
    breakdown = pd.DataFrame.from_dict(raw["holders"]["major_holders"], orient="index")
    breakdown = breakdown.rename(columns={breakdown.columns[0]: "Value"})
    return {
        "info": raw["info"],
        "groups": groups,
        "income": statement(fin["income_stmt"]),
        "balance": statement(fin["balance_sheet"]),
        "history": history,
        "institutions": institutions,
        "breakdown": breakdown,
    }


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
    import app.web.option_routes
    import app.web.portfolio_routes

    clock = Clock(OPTIONS_NOW)
    modules = (
        app.providers.yahoo,
        app.services.options,
        app.web.option_routes,
        app.web.portfolio_routes,
    )
    for module in modules:
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
        # Portfolio (Phase 4 part B)
        self.option_closes: dict[tuple[str, date], float] = {}
        self.close_calls: list[tuple[str, date]] = []
        # Calendar (Phase 6)
        self.earnings: list[EarningsData] = []
        self.earnings_down = False
        self.earnings_calls: list[list[str]] = []
        # Valuation and holders (Phase 7). `research_down` holds what fails, e.g.
        # "profile:AAPL", "figures:AAPL", "group:sector:technology", "ownership:AAPL".
        self.research_down: set[str] = set()
        self.research_calls: list[str] = []
        self.extra_contracts: list = []  # added to every option chain (unusual activity tests)

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
        chain = chain_from_yahoo(
            symbol, expiration, sample["calls"], sample["puts"], sample["underlying"]
        )
        return replace(chain, contracts=chain.contracts + tuple(self.extra_contracts))

    def get_daily_close(self, symbol: str, day: date) -> float | None:
        """Stocks: the recorded sample's close for that day. Options: `option_closes`."""
        self.close_calls.append((symbol, day))
        self._check_options(symbol)
        if len(symbol) > 12:  # an option contract symbol
            return self.option_closes.get((symbol, day))
        history, _ = load_yahoo_history()
        return close_on(history, day)

    def get_earnings(self, symbols, start, end) -> list[EarningsData]:
        self.earnings_calls.append(list(symbols))
        if self.mode != "ok" or self.earnings_down:
            raise MarketDataError("Yahoo Finance did not respond properly (Timeout).")
        return [e for e in self.earnings if e.symbol in symbols and start <= e.day <= end]

    def _research(self, what: str) -> dict:
        self.research_calls.append(what)
        if self.mode in ("down", "crash") or what in self.research_down:
            raise MarketDataError("Yahoo Finance did not respond properly (Timeout).")
        return load_yahoo_research()

    def get_profile(self, symbol: str):
        info = self._research(f"profile:{symbol}")["info"].get(symbol)
        if info is None:
            raise TickerNotFound(symbol)
        return profile_from_info(symbol, info)

    def get_annual_figures(self, symbol: str):
        s = self._research(f"figures:{symbol}")
        return annual_figures_from_yahoo(s["income"], s["balance"], s["history"])

    def get_group_members(self, kind: str, key: str):
        found = self._research(f"group:{kind}:{key}")["groups"].get(f"{kind}:{key}")
        if found is None:
            raise MarketDataError(f"Yahoo Finance listed no companies for the {kind} '{key}'.")
        return group_from_yahoo(kind, key, *found)

    def get_ownership(self, symbol: str):
        s = self._research(f"ownership:{symbol}")
        return ownership_from_yahoo(symbol, s["institutions"], s["breakdown"])

    def get_risk_free_rate(self) -> float:
        self.rate_calls += 1
        if self.rate_down or self.mode in ("down", "crash"):
            raise MarketDataError("Yahoo Finance did not respond properly (Timeout).")
        return FAKE_RISK_FREE_RATE


@pytest.fixture(autouse=True)
def test_host_allowed(monkeypatch):
    """Test clients send "Host: testserver", so test apps also answer to that name (F7.3).
    Tests that check the host rule pass `hosting=` to create_app themselves."""
    import app.main
    from app.config import HostingConfig

    names = [*HostingConfig().allowed_hosts, "testserver"]
    monkeypatch.setattr(
        app.main, "load_hosting_config", lambda path: HostingConfig(allowed_hosts=names)
    )


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
# A followed fund's 13F reports (Phase 7): the latest and the previous quarter.
FUND_CIK = 1067983
_FUND_FOLDER = "https://www.sec.gov/Archives/edgar/data/1067983/"
FUND_ROUTES = {
    "https://data.sec.gov/submissions/CIK0001067983.json": "sec13f_submissions.json",
    _FUND_FOLDER + "000095012326008001/index.json": "sec13f_index_latest.json",
    _FUND_FOLDER + "000095012326008001/46994.xml": "sec13f_infotable_latest.xml",
    _FUND_FOLDER + "000095012326005001/index.json": "sec13f_index_previous.json",
    _FUND_FOLDER + "000095012326005001/infotable.xml": "sec13f_infotable_previous.xml",
}

DAMODARAN_ROUTES = {
    "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/" + name: f"../damodaran/{name}"
    for name in ("pedata.html", "vebitda.html", "psdata.html")
}

DEFAULT_ROUTES = {
    **FUND_ROUTES,
    **DAMODARAN_ROUTES,
    "https://news.google.com/rss/search": "google_news_aapl.xml",
    "https://www.youtube.com/feeds/videos.xml?channel_id=UCsampleChannelAAAAAAAAA": (
        "youtube_channel.xml"
    ),
    "https://blog.example.com/feed/": "blog_feed.xml",
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
    import app.sources.x
    import app.sources.youtube

    for module in (
        app.sources.edgar,
        app.sources.news_rss,
        app.sources.x,
        app.sources.youtube,
        app.services.reports,
    ):
        monkeypatch.setattr(module, "utc_now", lambda: SOURCES_NOW)
    return SOURCES_NOW


# --- X (Phase 8): a fake X server -----------------------------------------------------


FAKE_BEARER = "fake-test-value"  # stands in for X_BEARER_TOKEN


class FakeX:
    """Stands in for X's recent search. Searches with "from:" get the trusted-accounts sample,
    others the $TICKER sample. Set `status` to answer with an error, `answer` to send other
    bytes, `empty` for no posts. Records every request's query parameters in `requests`."""

    def __init__(self) -> None:
        self.status = 200
        self.answer: bytes | None = None
        self.empty = False
        self.requests: list[dict[str, str]] = []

    def handler(self, request):
        import httpx

        self.requests.append(dict(request.url.params))
        if self.status != 200:
            return httpx.Response(self.status, json={"title": "Error", "detail": "Sample error"})
        if self.answer is not None:
            return httpx.Response(200, content=self.answer)
        query = request.url.params.get("query", "")
        name = (
            "x_search_empty.json"
            if self.empty
            else ("x_search_accounts.json" if "from:" in query else "x_search_ticker.json")
        )
        return httpx.Response(200, content=(SOURCE_FIXTURES / name).read_bytes())

    def api(self, token: str = FAKE_BEARER):
        import httpx

        from app.sources.x import XApi

        return XApi(token, transport=httpx.MockTransport(self.handler))


@pytest.fixture
def fake_x():
    return FakeX()


# --- the AI (Phase 3): always faked in tests ------------------------------------------


class FakeLLM:
    """Stands in for Gemini. Rates items by simple keyword rules so answers are predictable."""

    model_name = "fake-model"

    def __init__(self) -> None:
        self.fail: str | None = None  # set to a message to make every call raise AIError
        self.skip_refs: set[int] = set()  # items it pretends aren't about the ticker
        self.extra_claims: list[Claim] = []  # added to developments (e.g. uncited ones)
        # Added to the bottom line and to the bull and bear cases (owner 2026-11-14).
        self.extra_synthesis: list[Claim] = []
        self.item_calls: list[tuple[str, str | None, list[PublicItem]]] = []
        self.report_calls: list[tuple[str, str | None, list[PublicStory]]] = []
        # Videos (SPEC F4.5)
        self.video_calls: list[PublicVideo] = []
        self.video_fail: str | None = None  # AIError for video calls only
        self.unwatchable: set[str] = set()  # video URLs it can't watch
        self.video_seconds = 0.0  # pretend each video takes this long to watch
        self.most_at_once = 0  # most videos watched at the same time
        self._watching = 0
        self._lock = threading.Lock()

    def watch_video(self, video: PublicVideo):
        if self.fail or self.video_fail:
            raise AIError(self.fail or self.video_fail)
        with self._lock:
            self.video_calls.append(video)
            self._watching += 1
            self.most_at_once = max(self.most_at_once, self._watching)
        try:
            time.sleep(self.video_seconds)
            if video.url in self.unwatchable:
                raise VideoUnavailable("The AI service couldn't watch this video.")
            return (
                VideoPoint(f"First point of: {video.title}", 75),
                VideoPoint("Second point, time unknown.", None),
                VideoPoint("Third point, past the hour.", 3725),
            )
        finally:
            with self._lock:
                self._watching -= 1

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

        def titled(prefix, words):
            return [
                Claim(f"{prefix}: {s.title}.", (s.ref,))
                for s in stories
                if any(w in s.title.lower() for w in words)
            ]

        confirmed = [s for s in stories if not s.is_rumor]
        extra = list(self.extra_synthesis)
        return ReportDraft(
            developments=tuple(developments[:max_developments] + self.extra_claims),
            rumors=tuple(rumors[:max_rumors]),
            bottom_line=tuple(
                extra + [Claim(f"Bottom line: {s.title}.", (s.ref,)) for s in confirmed[:2]]
            ),
            bull_case=tuple(extra + titled("Bull", ("record", "beat", "buy"))),
            bear_case=tuple(extra + titled("Bear", ("slip", "worr", "sell", "recall"))),
        )


@pytest.fixture
def fake_llm():
    return FakeLLM()


# --- the whole app ------------------------------------------------------------------

TEST_SEC_USER_AGENT = "Test Person test.person@mail.test"


@pytest.fixture
def market_facts(fake_market_data, fake_fetch):
    """Valuation, holders and options (Phase 7) from the fake Yahoo, the sample Damodaran
    pages and one followed fund's sample 13F reports."""
    from app.config import (
        DamodaranConfig,
        FollowedFund,
        FollowedFundsConfig,
        InstitutionalConfig,
        load_benchmarks_config,
    )
    from app.providers.damodaran import DamodaranClient
    from app.providers.sec_13f import ThirteenFClient
    from app.services.market_facts import MarketFacts

    return MarketFacts(
        market_data=fake_market_data,
        benchmarks=load_benchmarks_config(PROJECT_ROOT / "config" / "benchmarks.yaml"),
        damodaran=DamodaranClient(fake_fetch, DamodaranConfig()),
        funds=ThirteenFClient(fake_fetch, TEST_SEC_USER_AGENT),
        institutional=InstitutionalConfig(
            followed_funds=FollowedFundsConfig(
                funds=[FollowedFund(name="Berkshire Hathaway", cik=FUND_CIK)]
            )
        ),
        max_parallel=2,
    )


@pytest.fixture
def report_services(fake_fetch, fake_llm, sources_now, app_config, market_facts):
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
        facts=market_facts,
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


# --- the calendar (Phase 6) -----------------------------------------------------------


class FakeFred:
    """Stands in for FRED: release id -> dates. Set `down` to fail, `is_set_up` to False for
    "no key"."""

    def __init__(self, dates: dict[int, list[date]] | None = None) -> None:
        self.is_set_up = True
        self.down = False
        self.dates: dict[int, list[date]] = dates or {}
        self.calls: list[int] = []
        # Macro dashboard (Phase 9): series id -> (date, value) points.
        self.series: dict[str, list[tuple[date, float]]] = {}
        self.series_calls: list[str] = []

    def release_dates(self, release_id, start, end):
        from app.providers.fred import FredError

        self.calls.append(release_id)
        if self.down:
            raise FredError("FRED didn't answer in time.")
        return [d for d in self.dates.get(release_id, []) if start <= d <= end]

    def series_observations(self, series_id, start):
        from app.providers.fred import FredError

        self.series_calls.append(series_id)
        if self.down:
            raise FredError("FRED didn't answer in time.")
        if series_id not in self.series:
            raise FredError(
                f"FRED has no series called {series_id}: check its id in config/macro.yaml."
            )
        return [p for p in self.series[series_id] if p[0] >= start]


# Fed meetings for tests (invented, near the calendar clock below).
TEST_FOMC = {
    "meetings": [
        {"decision": "2026-10-28", "minutes": "2026-11-18"},
        {"decision": "2026-12-09"},  # minutes three weeks later: Dec 30
    ]
}


@pytest.fixture
def fake_fred():
    return FakeFred(
        {
            10: [date(2026, 11, 12), date(2026, 12, 10)],  # CPI
            50: [date(2026, 11, 6), date(2026, 12, 4)],  # jobs report
            54: [date(2026, 11, 25)],  # PCE
            53: [date(2026, 11, 25)],  # GDP
        }
    )


@pytest.fixture
def fomc():
    from app.config import FomcConfig

    return FomcConfig.model_validate(TEST_FOMC)


# --- the macro dashboard (Phase 9) ---------------------------------------------------------


def company(symbol, cap, forward_pe=None, country="United States", name=None, trailing_pe=None):
    """A made-up company's Yahoo figures (market value in billions, for readability)."""
    from app.providers.market_data import CompanyProfile, Multiples

    return CompanyProfile(
        symbol=symbol,
        name=name or f"{symbol} Inc.",
        sector=None,
        sector_key=None,
        industry=None,
        industry_key=None,
        price=100.0,
        market_cap=cap,
        shares_outstanding=None,
        multiples=Multiples(trailing_pe=trailing_pe, forward_pe=forward_pe),
        country=country,
    )


class FakeMacroMarket:
    """Stands in for Yahoo on the macro page: sector lists (biggest first) and company
    figures. Unknown symbols fail like a Yahoo error."""

    def __init__(self, sectors=None, profiles=None) -> None:
        self.sectors: dict[str, list[str]] = sectors or {}
        self.profiles: dict = {p.symbol: p for p in profiles or []}
        self.down = False
        self.calls: list[str] = []

    def get_group_members(self, kind, key):
        from app.config import YAHOO_SECTORS
        from app.providers.market_data import GroupMembers

        if self.down:
            raise MarketDataError("Yahoo Finance did not respond properly (Timeout).")
        return GroupMembers(kind, key, YAHOO_SECTORS[key], tuple(self.sectors.get(key, [])))

    def get_profile(self, symbol):
        self.calls.append(symbol)
        if self.down:
            raise MarketDataError("Yahoo Finance did not respond properly (Timeout).")
        if symbol not in self.profiles:
            raise TickerNotFound(symbol)
        return self.profiles[symbol]


# Fri Nov 6, 2026, 10:00 AM US Eastern.
MACRO_NOW = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)


def weekdays(start: date, end: date, value) -> list[tuple[date, float]]:
    from datetime import timedelta

    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append((day, value(day)))
        day += timedelta(days=1)
    return days


def month_starts(first: date, last: date, value) -> list[tuple[date, float]]:
    points, year, month = [], first.year, first.month
    while (year, month) <= (last.year, last.month):
        day = date(year, month, 1)
        points.append((day, value(day)))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return points


def _cpi(day: date) -> float:
    # Known answers: Sep 2025 300.0 -> Sep 2026 309.0 (+3.0%); Aug 2025 300.0 -> Aug 2026
    # 308.1 (+2.7%). Other months: a steady made-up climb.
    known = {
        date(2025, 8, 1): 300.0,
        date(2025, 9, 1): 300.0,
        date(2026, 8, 1): 308.1,
        date(2026, 9, 1): 309.0,
    }
    return known.get(day, 250.0 + (day.year - 2019) * 8 + day.month * 0.5)


# Invented FRED series for the macro tests, up to the day before MACRO_NOW.
MACRO_SERIES = {
    # 0.40 until the end of October 2026, then 0.55: latest 0.55 (Nov 5), a month before
    # (Oct 5) 0.40, so the change is +0.15 points.
    "T10Y2Y": weekdays(
        date(2020, 6, 1), date(2026, 11, 5), lambda d: 0.55 if d >= date(2026, 11, 1) else 0.40
    ),
    "CPIAUCSL": month_starts(date(2019, 9, 1), date(2026, 9, 1), _cpi),
    "DGS10": weekdays(date(2020, 6, 1), date(2026, 11, 5), lambda d: 4.25),
    # Quarterly, millions of dollars: Jan 2026 7.0 trillion, Apr 2026 7.1 trillion (+1.4%).
    "MMMFFAQ027S": [
        (date(y, m, 1), 7_100_000.0 if (y, m) == (2026, 4) else 7_000_000.0 - (2026 - y) * 1e5)
        for y in range(2020, 2027)
        for m in (1, 4, 7, 10)
        if (y, m) <= (2026, 4)
    ],
}

# The forward P/E sample: the largest 10 US companies, picked from Yahoo's sector lists.
# Known answer: those with a forward P/E are worth 14,450 together and earn 579 (each
# company's value / its P/E: 125+150+64+40+20+80+50+20+30), so their combined forward
# P/E is 14,450 / 579 = 24.96.
# BRK-B has no forward P/E; TSM (based in Taiwan) is left out; GOOG and GOOGL are the same
# company; BROKEN fails at Yahoo; the small T/C/F/H companies never make the top 10.
MACRO_COMPANIES = [
    company("NVDA", 5000, 40),
    company("TSM", 1500, 20, country="Taiwan"),
    company("MSFT", 1400, 35),
    company("ORCL", 600, 30),
    company("T1", 50, 20),
    company("T2", 40, 20),
    company("T3", 30, 20),
    company("GOOGL", 3000, 20, name="Alphabet Inc."),
    company("GOOG", 3000, 20, name="Alphabet Inc."),
    company("META", 1600, 25),
    company("NFLX", 400, 40),
    company("C1", 45, 20),
    company("C2", 35, 20),
    company("BRK-B", 1000, None),
    company("JPM", 800, 10),
    company("V", 700, 14),
    company("F1", 48, 20),
    company("LLY", 900, 45),
    company("JNJ", 450, 15),
    company("H1", 20, 20),
    company("SPY", 600, 22, trailing_pe=25.0),  # P/E 25 -> earnings yield 4.00%
]
MACRO_SECTORS = {
    "technology": ["NVDA", "TSM", "MSFT", "ORCL", "T1", "T2", "T3"],
    "communication-services": ["GOOGL", "GOOG", "META", "NFLX", "C1", "C2"],
    "financial-services": ["BRK-B", "JPM", "V", "F1"],
    "healthcare": ["LLY", "BROKEN", "JNJ", "H1"],
}

# A small macro page for tests: one card of each kind.
TEST_MACRO = {
    "largest_forward_pe": {"companies": 10},
    "indicators": [
        {"label": "Yield curve: 10-year minus 2-year", "group": "Rates", "series": "T10Y2Y"},
        {
            "label": "CPI inflation",
            "group": "Inflation",
            "series": "CPIAUCSL",
            "show": "yearly_change",
            "decimals": 1,
        },
        {
            "label": "Money market fund assets (weekly)",
            "group": "Money",
            "source": "ici",
            "unit": "usd_billions",
        },
        {
            "label": "Money market fund assets (quarterly)",
            "group": "Money",
            "series": "MMMFFAQ027S",
            "unit": "usd_millions",
        },
        {"label": "Equity risk premium", "group": "Valuation", "source": "equity_risk_premium"},
        {
            "label": "S&P 500 forward P/E (estimate)",
            "group": "Valuation",
            "source": "largest_forward_pe",
            "unit": "ratio",
            "decimals": 1,
        },
    ],
}
ICI_PAGE = "https://www.ici.org/research/stats/mmf"


@pytest.fixture
def macro_market():
    return FakeMacroMarket(MACRO_SECTORS, MACRO_COMPANIES)


@pytest.fixture
def macro_fetch():
    return FakeFetch({ICI_PAGE: "ici_mmf_page.html"})


@pytest.fixture
def macro_setup(fake_fred, macro_market, macro_fetch):
    """The macro page with fakes, refreshing before the page is shown (no thread)."""
    from app.config import MacroConfig
    from app.providers.ici import IciClient
    from app.services.macro import MacroSetup

    fake_fred.series = {k: list(v) for k, v in MACRO_SERIES.items()}
    config = MacroConfig.model_validate(TEST_MACRO)
    return MacroSetup(
        fred=fake_fred,
        ici=IciClient(macro_fetch, config.ici.page_url),
        market_data=macro_market,
        config=config,
        parallel=1,
        background=False,
    )


@pytest.fixture
def client(
    engine,
    fake_market_data,
    app_config,
    report_services,
    report_runner,
    fake_fred,
    fomc,
    macro_setup,
):
    app = create_app(
        engine=engine,
        market_data=fake_market_data,
        app_config=app_config,
        report_services=report_services,
        report_runner=report_runner,
        fred=fake_fred,
        fomc=fomc,
        macro=macro_setup,
    )
    with TestClient(app) as c:  # `with` runs startup (creates the default user)
        yield c
