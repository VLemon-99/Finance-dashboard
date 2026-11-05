"""X posts in reports (SPEC F4.3, F4.6, F4.11; Phase 8 "done when"): X appears with its
weight, trusted accounts are read before any $TICKER search, a used-up budget is listed as
missing, and switching X off removes it cleanly. Fake internet, fake X, fake AI."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import SourcesConfig, XConfig
from app.main import create_app
from app.sources.registry import add_x_sources
from app.sources.x import XLedger
from tests import test_reports
from tests.test_reports import latest_report, run_for
from tests.test_web_reports import generate

# The report tests' fixtures, shared.
user_id = test_reports.user_id
services = test_reports.services


def with_x(services, engine, fake_x, **changes):
    """The report services plus X (trusted accounts and the $TICKER search)."""
    config = XConfig(enabled=True, accounts=["ExampleAnalyst", "SampleInvestor"], **changes)
    sources = replace(services.sources, sources=list(services.sources.sources), late_sources=[])
    add_x_sources(sources, fake_x.api(), XLedger(engine, config), config, SourcesConfig())
    return replace(services, sources=sources)


def test_x_posts_appear_in_the_report_with_their_weight(
    session,
    engine,
    user_id,
    services,
    fake_x,
):
    run_for(session, engine, user_id, with_x(services, engine, fake_x))
    body = latest_report(session).body

    posts = [s for s in body["stories"] if s["kind"] == "post"]
    assert len(posts) == 4  # 2 from trusted accounts + 2 from the search (spam skipped)
    outlets = {s["items"][0]["outlet"] for s in posts}
    assert outlets == {"@ExampleAnalyst", "@SampleInvestor", "X ($AAPL search)"}
    assert all(s["items"][0]["url"].startswith("https://x.com/i/web/status/") for s in posts)
    rumor = next(s for s in posts if s["title"].startswith("Rumor:"))
    assert rumor["is_rumor"] is True

    sentiment = body["sentiment"]
    assert sentiment["weights"] == {"filing": 1.0, "news": 1.0, "x": 0.5}
    # Known answer. News and filings (weight 1.0): 2 bullish, 6 neutral. X posts (weight 0.5):
    # 1 bullish ("...to buy a streaming service"), 1 bearish ("...selling price"), 2 neutral.
    # 100 x (2 + 0.5 - 0.5) / (8 x 1.0 + 4 x 0.5) = 200 / 10 = 20.
    assert sentiment["counts"] == {"bullish": 3, "neutral": 8, "bearish": 1}
    assert sentiment["score"] == 20

    # Spending: trusted accounts 2 x $0.005 + 2 x $0.010, search 3 x $0.005 = $0.045.
    assert body["x_spending"]["today_usd"] == pytest.approx(0.045)
    assert body["x_spending"]["monthly_budget_usd"] == 5.0


def test_x_weight_known_answer():
    from app.services.sentiment import sentiment_score

    weights = {"news": 1.0, "x": 0.5}
    # One bullish news story and one bearish X post: 100 x (1 - 0.5) / 1.5 = 33.3 -> 33.
    result = sentiment_score([("news", "bullish"), ("x", "bearish")], weights)
    assert result.score == 33
    # Two bullish X posts against one bearish news story: 100 x (1 - 1) / 2 = 0.
    assert (
        sentiment_score([("x", "bullish"), ("x", "bullish"), ("news", "bearish")], weights).score
        == 0
    )


def test_trusted_accounts_are_read_for_every_ticker_before_any_search(
    session,
    engine,
    user_id,
    services,
    fake_x,
):
    run_for(session, engine, user_id, with_x(services, engine, fake_x), tickers=("AAPL", "MSFT"))
    kinds = [
        "accounts" if "from:" in r["query"] else r["query"].split()[0] for r in fake_x.requests
    ]
    assert kinds == ["accounts", "accounts", "$AAPL", "$MSFT"]


def test_used_up_budget_is_listed_as_missing_and_the_report_still_made(
    session,
    engine,
    user_id,
    services,
    fake_x,
):
    run = run_for(session, engine, user_id, with_x(services, engine, fake_x, daily_budget_usd=0))
    assert run.status == "done"
    report = latest_report(session)
    assert "X: trusted accounts: skipped: today's X budget ($0.00) is used up; " in (
        " ".join(report.missing)
    )
    assert any(m.startswith("X: $TICKER search: skipped") for m in report.missing)
    assert fake_x.requests == []
    assert not [s for s in report.body["stories"] if s["kind"] == "post"]


def test_x_switched_off_leaves_no_trace(session, engine, user_id, services):
    run_for(session, engine, user_id, services)
    report = latest_report(session)
    assert "x" not in report.body["sentiment"]["weights"]
    assert report.body["x_spending"] is None
    assert not [m for m in report.missing if m.startswith("X")]
    assert not [s for s in report.body["stories"] if s["kind"] == "post"]


def test_report_page_shows_x_posts_and_spending(
    engine, fake_market_data, app_config, report_services, report_runner, fake_fred, fomc, fake_x
):
    app = create_app(
        engine=engine,
        market_data=fake_market_data,
        app_config=app_config,
        report_services=with_x(report_services, engine, fake_x),
        report_runner=report_runner,
        fred=fake_fred,
        fomc=fomc,
    )
    with TestClient(app) as client:
        html = client.get(generate(client, ticker="AAPL")).text
    assert '<span class="badge plain">X</span>' in html
    assert "@ExampleAnalyst" in html
    assert "x 0.5" in html
    assert "X spending so far: $0.04 of $0.22 today, $0.04 of $5.00 this month" in html
