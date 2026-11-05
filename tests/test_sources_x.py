"""X (Twitter) source (SPEC F4.3, F4.6, DECISIONS 2026-11-05): trusted accounts, the
$TICKER search, the hard budgets and the on/off switch. A fake X server answers with
hand-made samples in X's format (tests/fixtures/sources/x_search_*.json); no network."""

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from app.config import (
    PROJECT_ROOT,
    Settings,
    SourcesConfig,
    XConfig,
    load_x_config,
)
from app.db.models import XCheck, XSpending
from app.sources.base import SourceError
from app.sources.registry import build_sources
from app.sources.x import (
    MAX_QUERY_CHARS,
    NOT_SET_UP,
    XAccountsSource,
    XLedger,
    XTickerSearchSource,
    account_groups,
    accounts_query,
    cashtag,
    count_cashtags,
    mention_terms,
    parse_search,
    post_title,
)
from tests.conftest import FAKE_BEARER, SOURCE_FIXTURES, SOURCES_NOW

WEEK_AGO = SOURCES_NOW - timedelta(days=7)
ENV_EXAMPLE_VALUE = "your-x-bearer-token-here"  # X_BEARER_TOKEN in .env.example


def x_config(**changes) -> XConfig:
    values = {"enabled": True, "accounts": ["ExampleAnalyst", "SampleInvestor"], **changes}
    return XConfig(**values)


def accounts_source(engine, fake_x, config=None, token=FAKE_BEARER):
    config = config or x_config()
    return XAccountsSource(fake_x.api(token), XLedger(engine, config), config, {})


def search_source(engine, fake_x, config=None, token=FAKE_BEARER):
    config = config or x_config()
    return XTickerSearchSource(fake_x.api(token), XLedger(engine, config), config)


def spending(engine) -> list[XSpending]:
    with Session(engine) as session:
        return list(session.exec(select(XSpending).order_by(XSpending.day)).all())


def ten_posts() -> bytes:
    """An answer with 10 posts (the most one search asks for), each costing $0.005."""
    posts = [
        {
            "id": str(1985000000000000100 + n),
            "text": f"$AAPL post {n}",
            "created_at": "2026-11-02T08:00:00.000Z",
            "author_id": "9",
        }
        for n in range(10)
    ]
    return json.dumps({"data": posts, "meta": {"result_count": 10}}).encode()


# --- settings ---------------------------------------------------------------------------


def test_shipped_settings_file_is_off_with_no_accounts():
    config = load_x_config(PROJECT_ROOT / "config" / "x.yaml")
    assert config.enabled is False  # off until the owner sets up a key
    assert config.accounts == []  # owner 2026-11-05: ship an empty list
    assert config.monthly_budget_usd == 5.0  # owner 2026-11-05: $5 a month
    assert config.ticker_search is True


def test_handles_are_cleaned_and_checked():
    config = XConfig(accounts=["@ExampleAnalyst", "sample_investor", "EXAMPLEANALYST"])
    assert config.accounts == ["exampleanalyst", "sample_investor"]
    with pytest.raises(ValidationError):
        XConfig(accounts=["not a handle"])
    with pytest.raises(ValidationError):
        XConfig(posts_per_search=5)  # X's smallest answer is 10 posts


# --- reading X's answer -----------------------------------------------------------------


def test_parse_search_reads_posts_authors_and_long_text():
    page = parse_search(json.loads((SOURCE_FIXTURES / "x_search_accounts.json").read_text()))
    assert page.users == 2
    first, second = page.posts
    assert first.post_id == "1985000000000000002"
    assert first.username == "ExampleAnalyst"
    assert first.created_at == datetime(2026, 11, 2, 9, 15, tzinfo=UTC)
    assert (first.likes, first.reposts) == (140, 12)
    # Long posts: the full text, not X's cut-off version.
    assert second.text.startswith("Short thread on $AAPL: iPhone units flat")


def test_parse_search_with_no_posts():
    page = parse_search({"meta": {"result_count": 0}})
    assert page.posts == [] and page.users == 0


# --- building searches -------------------------------------------------------------------


def test_mention_terms():
    assert mention_terms("AMZN", ["AMAZON COM", "AMAZON"]) == [
        "$AMZN",
        '"AMZN"',
        '"AMAZON COM"',
        '"AMAZON"',
    ]
    assert mention_terms("F", []) == ["$F"]  # one-letter tickers only as a cashtag
    assert mention_terms("BRK-B", ['Berkshire "Hathaway"']) == ['"BRK-B"', '"Berkshire Hathaway"']
    assert cashtag("BRK-B") is None


def test_long_account_lists_are_split_to_fit_xs_length_limit():
    accounts = [f"account_{n:07d}" for n in range(40)]  # 15 characters each
    terms = mention_terms("AAPL", ["Apple"])
    groups = account_groups(accounts, terms)
    assert len(groups) > 1
    assert [a for g in groups for a in g] == accounts
    assert all(len(accounts_query(g, terms)) <= MAX_QUERY_CHARS for g in groups)


def test_post_title_and_cashtag_count():
    assert post_title("short   post\nhere") == "short post here"
    assert len(post_title("x" * 300)) == 140 and post_title("x" * 300).endswith("…")
    assert count_cashtags("$AAPL vs $MSFT, not US$5 or $5") == 2


# --- trusted accounts ---------------------------------------------------------------------


def test_trusted_accounts_search(engine, fake_x, sources_now):
    items = accounts_source(engine, fake_x).fetch("AAPL", "Apple", WEEK_AGO)

    (request,) = fake_x.requests
    assert request["query"] == (
        '(from:exampleanalyst OR from:sampleinvestor) ($AAPL OR "AAPL" OR "Apple") -is:retweet'
    )
    assert request["expansions"] == "author_id"  # the authors' handles, for citations
    assert request["max_results"] == "10"
    assert request["end_time"] == "2026-11-02T11:59:30Z"
    assert request["start_time"] == "2026-10-26T12:01:00Z"  # inside X's 7-day window

    assert [i.outlet for i in items] == ["@ExampleAnalyst", "@SampleInvestor"]
    first = items[0]
    assert first.source == "x_accounts" and first.source_type == "x"
    assert first.url == "https://x.com/i/web/status/1985000000000000002"
    assert first.text.startswith("Apple's services margin")
    assert first.data["likes"] == 140

    # Cost: 2 posts x $0.005 + 2 account records x $0.010 = $0.03.
    (day,) = spending(engine)
    assert (day.day, day.posts, day.users) == (date(2026, 11, 2), 2, 2)
    assert day.cost_usd == pytest.approx(0.03)


def test_next_search_only_asks_for_newer_posts(engine, fake_x, sources_now, monkeypatch):
    import app.sources.x

    source = accounts_source(engine, fake_x)
    source.fetch("AAPL", "Apple", WEEK_AGO)
    later = SOURCES_NOW + timedelta(hours=3)
    monkeypatch.setattr(app.sources.x, "utc_now", lambda: later)
    source.fetch("AAPL", "Apple", later - timedelta(days=7))
    assert fake_x.requests[1]["start_time"] == fake_x.requests[0]["end_time"]


def test_no_trusted_accounts_means_no_request(engine, fake_x, sources_now):
    items = accounts_source(engine, fake_x, x_config(accounts=[])).fetch("AAPL", "Apple", WEEK_AGO)
    assert items == [] and fake_x.requests == []


# --- the $TICKER search ---------------------------------------------------------------------


def test_ticker_search(engine, fake_x, sources_now):
    items = search_source(engine, fake_x).fetch("AAPL", "Apple", WEEK_AGO)

    (request,) = fake_x.requests
    assert request["query"] == "$AAPL -is:retweet -is:reply lang:en"
    assert request["sort_order"] == "relevancy"
    assert "expansions" not in request  # authors would triple the cost
    # The cashtag spam post is skipped; the other two are kept.
    assert [i.url.rsplit("/", 1)[1] for i in items] == [
        "1985000000000000013",
        "1985000000000000012",
    ]
    assert {i.outlet for i in items} == {"X ($AAPL search)"}
    (day,) = spending(engine)
    assert day.cost_usd == pytest.approx(3 * 0.005)  # the spam post was paid for too


def test_ticker_search_skips_tickers_without_a_cashtag(engine, fake_x, sources_now):
    assert search_source(engine, fake_x).fetch("BRK-B", None, WEEK_AGO) == []
    assert fake_x.requests == []


def test_tickers_searched_longest_ago_go_first(engine, fake_x, sources_now):
    source = search_source(engine, fake_x)
    ledger = XLedger(engine, x_config())
    ledger.mark_checked("MSFT", "search", SOURCES_NOW, SOURCES_NOW - timedelta(days=1))
    ledger.mark_checked("AAPL", "search", SOURCES_NOW, SOURCES_NOW - timedelta(days=2))
    assert source.order(["MSFT", "AAPL", "NVDA", "AMZN"]) == ["NVDA", "AMZN", "AAPL", "MSFT"]


# --- the hard budgets (Phase 8 "done when": the cap is enforced) ------------------------------


def test_daily_budget_stops_requests_before_they_are_sent(engine, fake_x, sources_now):
    # Each search could cost $0.05 (10 posts); the sample costs $0.015.
    source = search_source(engine, fake_x, x_config(daily_budget_usd=0.06))
    source.fetch("AAPL", None, WEEK_AGO)  # $0.015 spent; $0.015 + $0.05 > $0.06
    with pytest.raises(SourceError, match=r"today's X budget \(\$0.06\) is used up"):
        source.fetch("MSFT", None, WEEK_AGO)
    assert len(fake_x.requests) == 1


def test_monthly_budget_counts_earlier_days_of_the_month(engine, fake_x, sources_now):
    with Session(engine) as session:
        session.add(XSpending(day=date(2026, 10, 31), cost_usd=4.99))  # last month: ignored
        session.commit()
    source = search_source(engine, fake_x)
    source.fetch("AAPL", None, WEEK_AGO)
    with Session(engine) as session:
        session.add(XSpending(day=date(2026, 11, 1), cost_usd=4.96))
        session.commit()
    # This month: $4.96 + $0.015 = $4.975; one more search could reach $5.025.
    with pytest.raises(SourceError, match=r"this month's X budget \(\$5.00\) is used up"):
        source.fetch("MSFT", None, WEEK_AGO)
    assert len(fake_x.requests) == 1


def test_spending_never_goes_over_the_monthly_budget(engine, fake_x, sources_now):
    fake_x.answer = ten_posts()  # every search costs the most it can: 10 x $0.005 = $0.05
    source = search_source(engine, fake_x, x_config(monthly_budget_usd=0.12, daily_budget_usd=1))
    stopped = 0
    for ticker in ["AAPL", "MSFT", "NVDA", "AMZN", "META"]:
        try:
            source.fetch(ticker, None, WEEK_AGO)
        except SourceError:
            stopped += 1
    assert len(fake_x.requests) == 2 and stopped == 3  # $0.10 spent; a 3rd could reach $0.15
    assert sum(d.cost_usd for d in spending(engine)) == pytest.approx(0.10)


def test_refused_request_costs_nothing(engine, fake_x, sources_now):
    for status, message in [
        (401, "X refused the key"),
        (402, "prepaid credit is used up"),
        (429, "spending limit set in X's developer console"),
    ]:
        fake_x.status = status
        with pytest.raises(SourceError, match=message):
            search_source(engine, fake_x).fetch("AAPL", None, WEEK_AGO)
    assert sum(d.cost_usd for d in spending(engine)) == 0
    with Session(engine) as session:
        assert session.exec(select(XCheck)).all() == []  # will be searched again next time


def test_unreadable_answer_stays_counted_at_the_most_it_could_cost(engine, fake_x, sources_now):
    fake_x.answer = b"not json"
    with pytest.raises(SourceError, match="couldn't read"):
        search_source(engine, fake_x).fetch("AAPL", None, WEEK_AGO)
    assert spending(engine)[0].cost_usd == pytest.approx(0.05)


def test_missing_key_is_reported_without_a_request(engine, fake_x, sources_now):
    with pytest.raises(SourceError, match="X_BEARER_TOKEN"):
        accounts_source(engine, fake_x, token="").fetch("AAPL", "Apple", WEEK_AGO)
    with pytest.raises(SourceError, match="X_BEARER_TOKEN"):  # .env.example's placeholder
        accounts_source(engine, fake_x, token=ENV_EXAMPLE_VALUE).fetch("AAPL", "Apple", WEEK_AGO)
    assert NOT_SET_UP.startswith("not set up")
    assert fake_x.requests == [] and spending(engine) == []


# --- the on/off switch ----------------------------------------------------------------------


def test_switch_decides_which_x_sources_exist(engine):
    settings = Settings(_env_file=None, x_bearer_token=FAKE_BEARER)
    off = build_sources(settings, SourcesConfig(), XConfig(enabled=False), engine)
    assert "x" not in off.source_types() and off.x_ledger is None and off.late_sources == []

    on = build_sources(settings, SourcesConfig(), x_config(), engine)
    assert [s.name for s in on.sources][-1] == "x_accounts"
    assert [s.name for s in on.late_sources] == ["x_search"]
    assert "x" in on.source_types() and on.x_ledger is not None

    no_search = build_sources(settings, SourcesConfig(), x_config(ticker_search=False), engine)
    assert no_search.late_sources == []


def test_search_with_no_posts_costs_nothing_and_is_remembered(engine, fake_x, sources_now):
    fake_x.empty = True
    assert search_source(engine, fake_x).fetch("AAPL", None, WEEK_AGO) == []
    assert spending(engine)[0].cost_usd == 0
    assert XLedger(engine, x_config()).checked_until("AAPL", "search") == datetime(
        2026, 11, 2, 11, 59, 30, tzinfo=UTC
    )


def test_posts_already_paid_for_are_kept_when_a_later_account_group_is_over_budget(
    engine, fake_x, sources_now
):
    accounts = [f"account_{n:07d}" for n in range(40)]  # needs several searches
    # Room for the first search only: it could cost 10 x $0.005 + 10 x $0.010 = $0.15 and
    # costs $0.03; the next could bring the day to $0.18, over $0.16.
    config = x_config(accounts=accounts, daily_budget_usd=0.16)
    items = accounts_source(engine, fake_x, config).fetch("AAPL", "Apple", WEEK_AGO)
    assert len(fake_x.requests) == 1 and len(items) == 2
    with Session(engine) as session:
        assert len(session.exec(select(XCheck)).all()) == 1  # the other groups wait
