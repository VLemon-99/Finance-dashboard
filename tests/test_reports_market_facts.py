"""Phase 7 in reports: valuation (F4.7), institutional holders, followed funds and unusual
options (F4.8) are saved with each ticker's report; failures are listed (F4.11) and nothing
of it reaches the AI (F4.13)."""

import pytest

from app.services import reports as reports_service
from app.services.users import ensure_default_user
from tests.test_reports import latest_report, run_for


@pytest.fixture
def user_id(session):
    return ensure_default_user(session, "owner@localhost").id


@pytest.fixture
def services(report_services):
    return report_services


def test_report_has_valuation_holders_funds_and_options(session, engine, user_id, services):
    run = run_for(session, engine, user_id, services, tickers=("AAPL", "AMZN"))
    assert run.status == "done"
    body = latest_report(session, "AAPL").body
    pe = body["valuation"]["rows"][0]
    assert (pe["label"], pe["now"], pe["industry"]["value"]) == ("P/E", 30.0, 15.0)
    assert body["valuation"]["damodaran"]["industry"] == "Electronics (Consumer & Office)"
    assert body["institutional"]["ownership"]["institutions_count"] == 6512
    assert body["institutional"]["funds"]["positions"][0]["status"] == "unchanged"
    assert body["unusual_options"] is not None
    assert not [m for m in latest_report(session, "AAPL").missing if "aluation" in m]

    amzn = latest_report(session, "AMZN").body
    assert amzn["valuation"]["sector_group"]["name"] == "Technology"  # owner's setting


def test_one_run_shares_peers_between_tickers(session, engine, user_id, services):
    run_for(session, engine, user_id, services, tickers=("AAPL", "MSFT"))
    calls = services.facts.market_data.research_calls
    assert calls.count("group:sector:technology") == 1


def test_yahoo_down_still_makes_the_report(session, engine, user_id, services):
    services.facts.market_data.mode = "down"
    run = run_for(session, engine, user_id, services)
    assert run.status == "done"
    report = latest_report(session)
    assert report.body["valuation"] is None and report.body["unusual_options"] is None
    assert report.body["stories"]  # news and filings are unaffected
    joined = " | ".join(report.missing)
    assert "Valuation: Yahoo Finance did not respond properly" in joined
    assert "Top institutional holders:" in joined
    assert "Unusual options activity:" in joined


def test_unexpected_crash_in_market_facts(session, engine, user_id, services, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("surprise")

    monkeypatch.setattr(services.facts, "gather", boom)
    run = run_for(session, engine, user_id, services)
    assert run.status == "done"
    assert reports_service.FACTS_FAILED in latest_report(session).missing


def test_market_numbers_never_reach_the_ai(session, engine, user_id, services, fake_llm):
    run_for(session, engine, user_id, services)
    sent = repr(fake_llm.item_calls) + repr(fake_llm.report_calls)
    for private_or_market in ("Berkshire", "Vanguard", "P/E", "Consumer Electronics"):
        assert private_or_market not in sent
