"""F7.2 (story F7-S2): login sessions are stored in the database, only as token hashes."""

from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlmodel import select

from app.config import PROJECT_ROOT, AccountsConfig, ConfigFileError, load_accounts_config
from app.db.models import LoginSession, User
from app.db.session import make_engine
from app.services.users import end_session, start_session, user_for_token

NOW = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)
BEFORE = "e1f7a2c3b4d5"  # the passwords migration (F7-S1)


@pytest.fixture
def users(session):
    a = User(email="alice@example.com")
    b = User(email="bob@example.com")
    session.add_all([a, b])
    session.commit()
    session.refresh(a)
    session.refresh(b)
    return a, b


def test_f7_2_start_session_returns_a_token_and_stores_only_its_hash(session, users):
    alice, _ = users
    token = start_session(session, alice.id, days=30, now=NOW)
    assert isinstance(token, str) and len(token) >= 32
    rows = session.exec(select(LoginSession)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == alice.id
    assert row.token_hash != token
    assert token not in row.token_hash
    # Nothing in the whole table row holds the token itself.
    raw = session.execute(text("SELECT * FROM login_sessions")).all()
    assert all(token not in str(value) for value in raw[0])


def test_f7_2_tokens_are_random(session, users):
    alice, _ = users
    tokens = {start_session(session, alice.id, days=30, now=NOW) for _ in range(5)}
    assert len(tokens) == 5


def test_f7_2_valid_token_finds_its_user(session, users):
    alice, bob = users
    token_a = start_session(session, alice.id, days=30, now=NOW)
    token_b = start_session(session, bob.id, days=30, now=NOW)
    assert user_for_token(session, token_a, now=NOW).id == alice.id
    assert user_for_token(session, token_b, now=NOW + timedelta(days=29)).id == bob.id


def test_f7_2_unknown_or_empty_token_finds_no_user(session, users):
    alice, _ = users
    start_session(session, alice.id, days=30, now=NOW)
    assert user_for_token(session, "not-a-real-token", now=NOW) is None
    assert user_for_token(session, "", now=NOW) is None
    assert user_for_token(session, None, now=NOW) is None


def test_f7_2_expired_token_finds_no_user(session, users):
    alice, _ = users
    token = start_session(session, alice.id, days=30, now=NOW)
    row = session.exec(select(LoginSession)).one()
    assert row.expires_at.replace(tzinfo=UTC) == NOW + timedelta(days=30)  # stored in UTC
    assert user_for_token(session, token, now=NOW + timedelta(days=30, seconds=-1)) is not None
    assert user_for_token(session, token, now=NOW + timedelta(days=30)) is None
    assert user_for_token(session, token, now=NOW + timedelta(days=90)) is None


def test_f7_2_times_given_in_another_zone_are_stored_as_utc(session, users):
    alice, _ = users
    from zoneinfo import ZoneInfo

    eastern_now = NOW.astimezone(ZoneInfo("America/New_York"))
    token = start_session(session, alice.id, days=1, now=eastern_now)
    row = session.exec(select(LoginSession)).one()
    assert row.created_at.replace(tzinfo=UTC) == NOW
    assert user_for_token(session, token, now=NOW + timedelta(hours=23)) is not None


def test_f7_2_ended_session_stops_working_others_keep_working(session, users):
    alice, _ = users
    phone = start_session(session, alice.id, days=30, now=NOW)
    laptop = start_session(session, alice.id, days=30, now=NOW)
    end_session(session, phone)
    assert user_for_token(session, phone, now=NOW) is None
    assert user_for_token(session, laptop, now=NOW).id == alice.id
    end_session(session, phone)  # ending twice is harmless
    end_session(session, "unknown")


def test_f7_2_session_needs_at_least_one_day(session, users):
    alice, _ = users
    with pytest.raises(ValueError):
        start_session(session, alice.id, days=0, now=NOW)


# --- config/accounts.yaml -----------------------------------------------------------


def test_f7_2_session_length_default_is_30_days():
    assert AccountsConfig().session_days == 30


def test_f7_2_real_accounts_config_has_the_session_length():
    loaded = load_accounts_config(PROJECT_ROOT / "config" / "accounts.yaml")
    assert loaded.session_days == 30


def test_f7_2_session_length_is_checked(tmp_path):
    path = tmp_path / "accounts.yaml"
    path.write_text("session_days: 7\n", encoding="utf-8")
    assert load_accounts_config(path).session_days == 7
    for bad in ("session_days: 0\n", "session_days: 1000\n", "session_days: soon\n"):
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(ConfigFileError):
            load_accounts_config(path)


# --- migration ----------------------------------------------------------------------


def test_f7_2_login_sessions_migration_up_down_up(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    engine = make_engine(url)

    command.upgrade(cfg, "head")
    tables = inspect(engine).get_table_names()
    assert "login_sessions" in tables
    columns = {c["name"] for c in inspect(engine).get_columns("login_sessions")}
    assert {"user_id", "token_hash", "created_at", "expires_at"} <= columns

    command.downgrade(cfg, BEFORE)
    assert "login_sessions" not in inspect(engine).get_table_names()
    assert "users" in inspect(engine).get_table_names()

    command.upgrade(cfg, "head")
    assert "login_sessions" in inspect(engine).get_table_names()
    engine.dispose()
