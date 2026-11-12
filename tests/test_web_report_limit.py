"""F7.8 (story F7-S9): users other than the owner can generate at most 2 reports per US
Eastern day; the owner has no limit."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import PROJECT_ROOT, AccountsConfig, ScryptCost, load_accounts_config
from app.db.models import ReportRun, User
from app.main import create_app
from app.services.users import hash_password

FAST = ScryptCost(n=2**4, r=1, p=1)
PASSWORD = "correct horse battery"  # noqa: S105 (fake test password)
# 9pm US Eastern on Nov 12 = 2am UTC on Nov 13.
EVENING = datetime(2026, 11, 13, 2, 0, tzinfo=UTC)
USED_UP = "used all 2 reports for today"


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    import app.services.reports
    import app.web.report_routes

    c = Clock(EVENING)
    for module in (app.web.report_routes, app.services.reports):
        monkeypatch.setattr(module, "utc_now", c)
    return c


def _app(
    engine,
    fake_market_data,
    app_config,
    report_services,
    report_runner,
    fake_fred,
    fomc,
    macro_setup,
    **accounts,
):
    return create_app(
        engine=engine,
        market_data=fake_market_data,
        app_config=app_config,
        report_services=report_services,
        report_runner=report_runner,
        fred=fake_fred,
        fomc=fomc,
        macro=macro_setup,
        accounts=AccountsConfig(scrypt=FAST, **accounts),
    )


@pytest.fixture
def app(
    engine,
    fake_market_data,
    app_config,
    report_services,
    report_runner,
    fake_fred,
    fomc,
    macro_setup,
    clock,
):
    report_runner.hold = True  # record runs only; the report pipeline has its own tests
    application = _app(
        engine,
        fake_market_data,
        app_config,
        report_services,
        report_runner,
        fake_fred,
        fomc,
        macro_setup,
    )
    with TestClient(application) as owner_browser:  # startup creates the owner
        application.state.owner_browser = owner_browser
        yield application


def _browser_for(app, engine, email):
    with Session(engine) as s:
        s.add(User(email=email, password_hash=hash_password(PASSWORD, FAST)))
        s.commit()
    browser = TestClient(app)
    response = browser.post(
        "/login", data={"email": email, "password": PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303
    return browser


@pytest.fixture
def alice(app, engine):
    return _browser_for(app, engine, "alice@example.com")


def generate(browser, engine, ticker="AAPL"):
    response = browser.post("/reports/generate", data={"ticker": ticker}, follow_redirects=False)
    with Session(engine) as s:  # finish it, so the next one isn't "already running"
        for run in s.exec(select(ReportRun).where(ReportRun.status == "running")):
            run.status = "done"
            s.add(run)
        s.commit()
    return response


def runs_of(engine, email):
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).one()
        return s.exec(select(ReportRun).where(ReportRun.user_id == user.id)).all()


def test_f7_8_two_reports_a_day_then_refused(app, engine, alice):
    assert generate(alice, engine).status_code == 303
    assert generate(alice, engine, "MSFT").status_code == 303
    third = generate(alice, engine, "NVDA")
    assert third.status_code == 429
    assert USED_UP in third.text
    assert len(runs_of(engine, "alice@example.com")) == 2


def test_f7_8_watchlist_reports_count_too(app, engine, alice):
    alice.post("/watchlists", data={"name": "Mine"}, follow_redirects=False)
    with Session(engine) as s:
        from app.db.models import Watchlist

        mine = s.exec(select(Watchlist).where(Watchlist.name == "Mine")).one()
    alice.post(f"/watchlists/{mine.id}/items", data={"ticker": "AAPL"})
    generate(alice, engine)
    response = alice.post(
        "/reports/generate", data={"watchlist_id": mine.id}, follow_redirects=False
    )
    assert response.status_code == 303
    generate(alice, engine)  # finishes the watchlist run
    response = alice.post(
        "/reports/generate", data={"watchlist_id": mine.id}, follow_redirects=False
    )
    assert response.status_code == 429


def test_f7_8_count_resets_at_midnight_us_eastern(app, engine, alice, clock):
    generate(alice, engine)
    generate(alice, engine)
    clock.now = EVENING + timedelta(hours=2, minutes=59)  # 11:59pm ET, 4:59am UTC
    assert generate(alice, engine).status_code == 429
    clock.now = EVENING + timedelta(hours=3)  # midnight ET
    assert generate(alice, engine).status_code == 303


def test_f7_8_utc_midnight_is_not_a_new_day(app, engine, alice, clock):
    clock.now = datetime(2026, 11, 12, 23, 30, tzinfo=UTC)  # 6:30pm ET
    generate(alice, engine)
    generate(alice, engine)
    clock.now = datetime(2026, 11, 13, 0, 30, tzinfo=UTC)  # new UTC day, still Nov 12 in ET
    assert generate(alice, engine).status_code == 429


def test_f7_8_owner_is_never_limited(app, engine):
    owner = app.state.owner_browser  # no cookie: the owner (default user)
    for ticker in ("AAPL", "MSFT", "NVDA", "TSLA"):
        assert generate(owner, engine, ticker).status_code == 303
    assert "reports left today" not in owner.get("/reports").text


def test_f7_8_owner_signed_in_is_never_limited(app, engine):
    with Session(engine) as s:
        owner = s.get(User, app.state.user_id)
        owner.password_hash = hash_password(PASSWORD, FAST)
        email = owner.email
        s.add(owner)
        s.commit()
    browser = TestClient(app)
    browser.post("/login", data={"email": email, "password": PASSWORD})
    for ticker in ("AAPL", "MSFT", "NVDA"):
        assert generate(browser, engine, ticker).status_code == 303


def test_f7_8_reports_page_says_how_many_are_left(app, engine, alice):
    assert "2 of 2 reports left today" in alice.get("/reports").text
    generate(alice, engine)
    assert "1 of 2 reports left today" in alice.get("/reports").text
    generate(alice, engine)
    assert "0 of 2 reports left today" in alice.get("/reports").text


def test_f7_8_one_users_runs_dont_count_for_another(app, engine, alice):
    bob = _browser_for(app, engine, "bob@example.com")
    generate(alice, engine)
    generate(alice, engine)
    assert generate(bob, engine).status_code == 303
    assert "1 of 2 reports left today" in bob.get("/reports").text


def test_f7_8_already_running_report_isnt_counted_again(app, engine, alice):
    first = alice.post("/reports/generate", data={"ticker": "AAPL"}, follow_redirects=False)
    again = alice.post("/reports/generate", data={"ticker": "MSFT"}, follow_redirects=False)
    assert again.headers["location"] == first.headers["location"]  # sent to the running one
    assert len(runs_of(engine, "alice@example.com")) == 1


def test_f7_8_limit_comes_from_config(
    engine,
    fake_market_data,
    app_config,
    report_services,
    report_runner,
    fake_fred,
    fomc,
    macro_setup,
    clock,
):
    report_runner.hold = True
    application = _app(
        engine,
        fake_market_data,
        app_config,
        report_services,
        report_runner,
        fake_fred,
        fomc,
        macro_setup,
        reports_per_day=1,
    )
    with TestClient(application):
        browser = _browser_for(application, engine, "carol@example.com")
        assert generate(browser, engine).status_code == 303
        response = generate(browser, engine)
        assert response.status_code == 429
        assert "used all 1 report for today" in response.text


def test_f7_8_default_limit_is_2():
    assert AccountsConfig().reports_per_day == 2
    assert load_accounts_config(PROJECT_ROOT / "config" / "accounts.yaml").reports_per_day == 2
