"""F7.2 (story F7-S1): passwords are stored only as salted scrypt hashes."""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.config import (
    PROJECT_ROOT,
    AccountsConfig,
    ConfigFileError,
    ScryptCost,
    load_accounts_config,
)
from app.db.session import make_engine
from app.services.users import check_password, hash_password

# Cheap settings so the tests run fast; the real defaults are tested separately.
FAST = ScryptCost(n=2**4, r=1, p=1)

BEFORE = "92cad3ca91ff"  # the last migration before passwords


# --- hashing -----------------------------------------------------------------------


def test_f7_2_same_password_hashed_twice_differs_and_hides_the_password():
    first = hash_password("correct horse battery", FAST)
    second = hash_password("correct horse battery", FAST)
    assert first != second  # a new random salt each time
    for stored in (first, second):
        assert "correct horse battery" not in stored
        assert stored.startswith("scrypt$")


def test_f7_2_right_password_checks_true():
    stored = hash_password("s3cret-Pass", FAST)
    assert check_password(stored, "s3cret-Pass") is True


def test_f7_2_wrong_or_empty_password_checks_false():
    stored = hash_password("s3cret-Pass", FAST)
    assert check_password(stored, "s3cret-pass") is False
    assert check_password(stored, "s3cret-Pass ") is False
    assert check_password(stored, "") is False


def test_f7_2_user_with_no_password_hash_checks_false():
    assert check_password(None, "anything") is False
    assert check_password("", "anything") is False


def test_f7_2_damaged_stored_hash_checks_false_instead_of_crashing():
    stored = hash_password("s3cret-Pass", FAST)
    for damaged in (
        "not-a-hash",
        stored.replace("scrypt$", "md5$"),
        stored.rsplit("$", 1)[0] + "$%%%",
        "scrypt$x$1$1$abc$abc",
        "scrypt$3$1$1$abc$abc",  # n must be a power of two
    ):
        assert check_password(damaged, "s3cret-Pass") is False


def test_f7_2_check_uses_the_cost_stored_with_the_hash():
    """Changing the config later must not lock out passwords set before."""
    stored = hash_password("s3cret-Pass", ScryptCost(n=2**5, r=2, p=1))
    assert stored.split("$")[1:4] == ["32", "2", "1"]
    assert check_password(stored, "s3cret-Pass") is True


def test_f7_2_empty_password_cannot_be_set():
    with pytest.raises(ValueError):
        hash_password("", FAST)


def test_f7_2_non_ascii_password_works():
    stored = hash_password("pässwörd-日本", FAST)
    assert check_password(stored, "pässwörd-日本") is True
    assert check_password(stored, "passwort-日本") is False


# --- config/accounts.yaml -----------------------------------------------------------


def test_f7_2_scrypt_cost_defaults():
    cost = AccountsConfig().scrypt
    assert (cost.n, cost.r, cost.p) == (2**15, 8, 1)


def test_f7_2_real_accounts_config_file_loads_with_the_defaults():
    loaded = load_accounts_config(PROJECT_ROOT / "config" / "accounts.yaml")
    assert loaded.scrypt == AccountsConfig().scrypt


def test_f7_2_real_defaults_hash_and_check():
    cost = AccountsConfig().scrypt
    stored = hash_password("s3cret-Pass", cost)
    assert check_password(stored, "s3cret-Pass") is True


def test_f7_2_accounts_config_rejects_bad_cost(tmp_path):
    bad = tmp_path / "accounts.yaml"
    for body in ("scrypt:\n  n: 1000\n", "scrypt:\n  r: 0\n", "scrypt:\n  typo: 3\n"):
        bad.write_text(body, encoding="utf-8")
        with pytest.raises(ConfigFileError):
            load_accounts_config(bad)


# --- migration ----------------------------------------------------------------------


def _alembic(url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    return cfg


def test_f7_2_password_migration_up_down_up_keeps_users(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = _alembic(url)
    command.upgrade(cfg, BEFORE)
    engine = make_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (email, created_at) VALUES ('fake@example.com', :t)"),
            {"t": "2026-01-01 00:00:00"},
        )

    def columns():
        return {c["name"] for c in inspect(engine).get_columns("users")}

    command.upgrade(cfg, "head")
    assert "password_hash" in columns()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT email, password_hash FROM users")).all()
    assert rows == [("fake@example.com", None)]

    command.downgrade(cfg, BEFORE)
    assert "password_hash" not in columns()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT email FROM users")).all() == [("fake@example.com",)]

    command.upgrade(cfg, "head")
    assert "password_hash" in columns()
    engine.dispose()


def test_f7_2_user_password_hash_is_stored(session):
    from app.db.models import User

    user = User(email="fake@example.com", password_hash=hash_password("s3cret-Pass", FAST))
    session.add(user)
    session.commit()
    session.refresh(user)
    assert check_password(user.password_hash, "s3cret-Pass") is True
    assert User(email="other@example.com").password_hash is None
