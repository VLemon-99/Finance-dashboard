"""F7.7 (story F7-S8): after 10 wrong passwords for one email within 15 minutes, password
logins for that email are refused for 15 minutes."""

from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlmodel import Session, select

from app.config import PROJECT_ROOT, AccountsConfig, ScryptCost, load_accounts_config
from app.db.models import LoginFailure, User
from app.db.session import make_engine
from app.main import create_app
from app.services.users import hash_password

FAST = ScryptCost(n=2**4, r=1, p=1)
EMAIL = "alice@example.com"
PASSWORD = "correct horse battery"  # noqa: S105 (fake test password)
WRONG = "wrong password"  # noqa: S105 (fake test password)
START = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)
LOCKED = "Too many wrong passwords for this email"


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


@pytest.fixture
def clock(monkeypatch):
    import app.web.account_routes

    c = Clock(START)
    monkeypatch.setattr(app.web.account_routes, "utc_now", c)
    return c


@pytest.fixture
def web(engine, fake_market_data, app_config, report_services, fake_fred, fomc, macro_setup, clock):
    app = create_app(
        engine=engine,
        market_data=fake_market_data,
        app_config=app_config,
        report_services=report_services,
        fred=fake_fred,
        fomc=fomc,
        macro=macro_setup,
        accounts=AccountsConfig(scrypt=FAST),
    )
    with TestClient(app) as client:
        with Session(engine) as s:
            s.add(User(email=EMAIL, password_hash=hash_password(PASSWORD, FAST)))
            s.add(User(email="bob@example.com", password_hash=hash_password(PASSWORD, FAST)))
            s.commit()
        yield client


def login(web, email=EMAIL, password=PASSWORD):
    return web.post("/login", data={"email": email, "password": password}, follow_redirects=False)


def fail(web, times, email=EMAIL, clock=None, every=None):
    for _ in range(times):
        assert login(web, email, WRONG).status_code == 401
        if clock and every:
            clock.advance(**every)


def test_f7_7_eleventh_attempt_is_refused_even_with_the_right_password(web, clock):
    fail(web, 10)
    response = login(web)
    assert response.status_code == 429
    assert LOCKED in response.text
    assert "15 minutes" in response.text
    assert "Sign in with Google" in response.text
    assert "set-cookie" not in response.headers


def test_f7_7_nine_wrong_tries_still_allow_the_right_password(web, clock):
    fail(web, 9)
    assert login(web).status_code == 303


def test_f7_7_lock_ends_after_the_lockout_time(web, clock):
    fail(web, 10)
    clock.advance(minutes=14, seconds=59)
    response = login(web)
    assert response.status_code == 429
    assert "Try again in 1 minute, or" in response.text
    clock.advance(seconds=1)
    assert login(web).status_code == 303


def test_f7_7_tries_spread_over_more_than_the_window_dont_lock(web, clock):
    fail(web, 10, clock=clock, every={"minutes": 2})  # 10 tries over 20 minutes
    assert login(web).status_code == 303


def test_f7_7_locked_attempts_dont_extend_the_lock(web, clock):
    fail(web, 10)
    for _ in range(5):
        clock.advance(minutes=2)
        assert login(web, password=WRONG).status_code == 429
    clock.advance(minutes=5)  # 15 minutes after the 10th wrong try
    assert login(web).status_code == 303


def test_f7_7_one_email_locked_doesnt_affect_another(web, clock):
    fail(web, 10)
    assert login(web, email="bob@example.com").status_code == 303


def test_f7_7_email_case_and_spaces_dont_matter(web, clock):
    for i in range(10):
        email = ["ALICE@example.com", " alice@EXAMPLE.com ", EMAIL][i % 3]
        assert login(web, email, WRONG).status_code == 401
    assert login(web, email="Alice@Example.Com").status_code == 429


def test_f7_7_successful_login_clears_the_count(web, engine, clock):
    fail(web, 9)
    assert login(web).status_code == 303
    web.cookies.clear()
    fail(web, 9)
    assert login(web).status_code == 303
    with Session(engine) as s:
        assert s.exec(select(LoginFailure)).all() == []


def test_f7_7_unknown_emails_are_counted_and_get_the_same_message(web, clock):
    fail(web, 10, email="nobody@example.com")
    unknown = login(web, email="nobody@example.com")
    fail(web, 10)
    known = login(web)
    assert unknown.status_code == known.status_code == 429
    assert unknown.text.replace("nobody@example.com", "X") == known.text.replace(EMAIL, "X")


def test_f7_7_failures_are_stored_in_utc_by_lower_case_email(web, engine, clock):
    login(web, " ALICE@example.com", WRONG)
    with Session(engine) as s:
        (row,) = s.exec(select(LoginFailure)).all()
    assert row.email == EMAIL
    assert row.failed_at.replace(tzinfo=UTC) == START


def test_f7_7_old_failures_are_cleaned_up(web, engine, clock):
    fail(web, 3)
    clock.advance(hours=2)
    fail(web, 1)
    with Session(engine) as s:
        assert len(s.exec(select(LoginFailure)).all()) == 1


def test_f7_7_numbers_come_from_config(
    engine, fake_market_data, app_config, report_services, fake_fred, fomc, macro_setup, clock
):
    accounts = AccountsConfig(
        scrypt=FAST, max_failed_logins=3, failed_login_window_minutes=5, lockout_minutes=60
    )
    app = create_app(
        engine=engine,
        market_data=fake_market_data,
        app_config=app_config,
        report_services=report_services,
        fred=fake_fred,
        fomc=fomc,
        macro=macro_setup,
        accounts=accounts,
    )
    with TestClient(app) as web:
        with Session(engine) as s:
            s.add(User(email=EMAIL, password_hash=hash_password(PASSWORD, FAST)))
            s.commit()
        fail(web, 3)
        clock.advance(minutes=59)
        assert login(web).status_code == 429
        clock.advance(minutes=1)
        assert login(web).status_code == 303


def test_f7_7_default_numbers():
    accounts = AccountsConfig()
    assert (
        accounts.max_failed_logins,
        accounts.failed_login_window_minutes,
        accounts.lockout_minutes,
    ) == (10, 15, 15)
    real = load_accounts_config(PROJECT_ROOT / "config" / "accounts.yaml")
    assert (real.max_failed_logins, real.failed_login_window_minutes, real.lockout_minutes) == (
        10,
        15,
        15,
    )


def test_f7_7_failures_migration_up_down_up(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    engine = make_engine(url)
    command.upgrade(cfg, "head")
    assert "login_failures" in inspect(engine).get_table_names()
    columns = {c["name"] for c in inspect(engine).get_columns("login_failures")}
    assert {"email", "failed_at"} <= columns
    command.downgrade(cfg, "f2a8b3c4d5e6")
    assert "login_failures" not in inspect(engine).get_table_names()
    command.upgrade(cfg, "head")
    assert "login_failures" in inspect(engine).get_table_names()
    engine.dispose()
