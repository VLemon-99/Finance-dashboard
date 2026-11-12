"""F7.9 (story F7-S10): the scheduled morning report runs only for the owner."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import AccountsConfig, AppConfig, ScryptCost
from app.db.models import Instrument, Report, ReportRun, User, Watchlist, WatchlistItem
from app.main import create_app
from app.scheduler import JOB_ID
from app.services.users import hash_password

FAST = ScryptCost(n=2**4, r=1, p=1)
PASSWORD = "correct horse battery"  # noqa: S105 (fake test password)


def _watchlist(session, user_id, name, ticker):
    stock = session.exec(select(Instrument).where(Instrument.symbol == ticker)).first()
    if stock is None:
        stock = Instrument(kind="stock", symbol=ticker)
        session.add(stock)
        session.commit()
    watchlist = Watchlist(user_id=user_id, name=name)
    session.add(watchlist)
    session.commit()
    session.add(WatchlistItem(user_id=user_id, watchlist_id=watchlist.id, instrument_id=stock.id))
    session.commit()


@pytest.fixture
def scheduled_app(
    engine, fake_market_data, report_services, report_runner, fake_fred, fomc, macro_setup
):
    config = AppConfig.model_validate({"schedule": {"enabled": True, "time": "07:30"}})
    app = create_app(
        engine=engine,
        market_data=fake_market_data,
        app_config=config,
        report_services=report_services,
        report_runner=report_runner,
        fred=fake_fred,
        fomc=fomc,
        macro=macro_setup,
        accounts=AccountsConfig(scrypt=FAST),
    )
    with TestClient(app) as client:
        owner_id = app.state.user_id
        with Session(engine) as s:
            alice = User(email="alice@example.com", password_hash=hash_password(PASSWORD, FAST))
            bob = User(email="bob@example.com")
            s.add_all([alice, bob])
            s.commit()
            _watchlist(s, owner_id, "Owner Picks", "AAPL")
            _watchlist(s, alice.id, "Alice Picks", "MSFT")
            _watchlist(s, bob.id, "Bob Picks", "NVDA")
        yield app, client


def test_f7_9_the_scheduled_job_is_for_the_owner_only(scheduled_app):
    app, _ = scheduled_app
    job = app.state.scheduler.get_job(JOB_ID)
    assert job.args[1] == app.state.user_id


def test_f7_9_a_scheduled_run_covers_only_the_owners_tickers(scheduled_app, engine):
    app, _ = scheduled_app
    job = app.state.scheduler.get_job(JOB_ID)
    run_ids = job.func(*job.args)  # what the scheduler does at 7:30am
    with Session(engine) as s:
        runs = s.exec(select(ReportRun)).all()
        assert [r.id for r in runs] == run_ids
        assert [(r.label, r.user_id) for r in runs] == [("Owner Picks", app.state.user_id)]
        reports = s.exec(select(Report)).all()
        assert {(r.ticker, r.user_id) for r in reports} == {("AAPL", app.state.user_id)}


def test_f7_9_other_users_still_generate_by_hand(scheduled_app, engine, report_runner):
    app, _ = scheduled_app
    report_runner.hold = True
    browser = TestClient(app)
    browser.post("/login", data={"email": "alice@example.com", "password": PASSWORD})
    response = browser.post("/reports/generate", data={"ticker": "MSFT"}, follow_redirects=False)
    assert response.status_code == 303
    with Session(engine) as s:
        alice = s.exec(select(User).where(User.email == "alice@example.com")).one()
        runs = s.exec(select(ReportRun).where(ReportRun.user_id == alice.id)).all()
        assert [r.label for r in runs] == ["MSFT"]


def test_f7_9_reports_page_tells_other_users_automatic_reports_are_the_owners(
    scheduled_app,
):
    app, owner_browser = scheduled_app
    assert "Automatic reports: <strong>on</strong>" in owner_browser.get("/reports").text
    browser = TestClient(app)
    browser.post("/login", data={"email": "alice@example.com", "password": PASSWORD})
    page = browser.get("/reports").text
    assert "Automatic reports: <strong>on</strong>" not in page
    assert "Automatic morning reports are only for the owner" in page
