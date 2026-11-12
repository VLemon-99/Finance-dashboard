"""F7.5 (story F7-S6): a command on the server sets the owner's email and password, and
resets anyone's forgotten password."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.accounts_cli import main
from app.config import AccountsConfig, ScryptCost
from app.db.models import User, Watchlist
from app.main import create_app
from app.services.users import check_password, ensure_owner

FAST = AccountsConfig(scrypt=ScryptCost(n=2**4, r=1, p=1))
GOOD = "a long enough password"  # noqa: S105 (fake test password)
OTHER = "another long password"  # noqa: S105 (fake test password)


class FakeGetpass:
    """Answers the password prompts in order and remembers the prompts."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        return self.answers.pop(0)


def run(engine, capsys, *args, answers=(GOOD, GOOD)):
    fake = FakeGetpass(*answers)
    code = main(list(args), getpass=fake, engine=engine, accounts=FAST)
    out = capsys.readouterr()
    return code, out.out + out.err, fake


@pytest.fixture
def owner_id(engine):
    with Session(engine) as s:
        owner = ensure_owner(s, "owner@localhost")
        s.add(Watchlist(user_id=owner.id, name="Owner Picks"))
        s.commit()
        return owner.id


def users(engine):
    with Session(engine) as s:
        return s.exec(select(User).order_by(User.id)).all()


def test_f7_5_owner_command_renames_owner_and_sets_password(engine, capsys, owner_id):
    code, output, fake = run(engine, capsys, "owner", "--email", "  Me@Example.com ")
    assert code == 0
    assert len(fake.prompts) == 2  # asked twice
    (owner,) = users(engine)
    assert owner.id == owner_id
    assert owner.email == "Me@Example.com"
    assert check_password(owner.password_hash, GOOD)
    assert GOOD not in output
    with Session(engine) as s:
        lists = s.exec(select(Watchlist)).all()
        assert [(w.name, w.user_id) for w in lists] == [("Owner Picks", owner_id)]


def test_f7_5_after_rename_the_app_keeps_the_same_owner(engine, capsys, owner_id, fake_market_data):
    run(engine, capsys, "owner", "--email", "me@example.com")
    for _ in range(2):  # restart the app twice
        app = create_app(engine=engine, market_data=fake_market_data)
        with TestClient(app):
            assert app.state.user_id == owner_id
    assert [u.email for u in users(engine)] == ["me@example.com"]


def test_f7_5_empty_database_still_gets_the_default_user(engine):
    with Session(engine) as s:
        owner = ensure_owner(s, "owner@localhost")
        again = ensure_owner(s, "owner@localhost")
    assert owner.id == again.id
    assert [u.email for u in users(engine)] == ["owner@localhost"]


def test_f7_5_owner_is_the_first_user_even_when_others_exist(engine, owner_id):
    with Session(engine) as s:
        s.add(User(email="someone@example.com"))
        s.commit()
        assert ensure_owner(s, "owner@localhost").id == owner_id
        assert ensure_owner(s, "different-default@localhost").id == owner_id


def test_f7_5_password_command_resets_an_existing_users_password(engine, capsys, owner_id):
    with Session(engine) as s:
        s.add(User(email="friend@example.com"))
        s.commit()
    code, output, _ = run(
        engine, capsys, "password", "--email", "FRIEND@example.com", answers=(OTHER, OTHER)
    )
    assert code == 0
    friend = users(engine)[1]
    assert check_password(friend.password_hash, OTHER)
    assert OTHER not in output
    assert users(engine)[0].password_hash is None  # the owner is untouched


def test_f7_5_password_command_for_unknown_email_changes_nothing(engine, capsys, owner_id):
    before = [u.model_dump() for u in users(engine)]
    code, output, _ = run(engine, capsys, "password", "--email", "nobody@example.com")
    assert code != 0
    assert "No account" in output
    assert [u.model_dump() for u in users(engine)] == before


@pytest.mark.parametrize(
    "answers, message",
    [
        (("short", "short"), "at least 12 characters"),
        ((GOOD, OTHER), "didn't match"),
        (("", ""), "at least 12 characters"),
    ],
)
@pytest.mark.parametrize("command", ["owner", "password"])
def test_f7_5_bad_passwords_are_refused_and_change_nothing(
    engine, capsys, owner_id, answers, message, command
):
    before = [u.model_dump() for u in users(engine)]
    code, output, _ = run(engine, capsys, command, "--email", "owner@localhost", answers=answers)
    assert code != 0
    assert message in output
    assert [u.model_dump() for u in users(engine)] == before
    for answer in answers:
        if answer:
            assert answer not in output


@pytest.mark.parametrize("bad", ["", "   ", "not-an-email", "a b@example.com", "x@@y"])
def test_f7_5_malformed_email_is_refused(engine, capsys, owner_id, bad):
    before = [u.model_dump() for u in users(engine)]
    code, output, fake = run(engine, capsys, "owner", "--email", bad)
    assert code != 0
    assert "email" in output.lower()
    assert fake.prompts == []  # refused before asking for a password
    assert [u.model_dump() for u in users(engine)] == before


def test_f7_5_owner_email_taken_by_another_user_is_refused(engine, capsys, owner_id):
    with Session(engine) as s:
        s.add(User(email="friend@example.com"))
        s.commit()
    before = [u.model_dump() for u in users(engine)]
    code, output, _ = run(engine, capsys, "owner", "--email", "Friend@Example.com")
    assert code != 0
    assert "already" in output
    assert [u.model_dump() for u in users(engine)] == before


def test_f7_5_owner_can_keep_the_same_email_and_change_password(engine, capsys, owner_id):
    run(engine, capsys, "owner", "--email", "me@example.com")
    code, _, _ = run(engine, capsys, "owner", "--email", "me@example.com", answers=(OTHER, OTHER))
    assert code == 0
    assert check_password(users(engine)[0].password_hash, OTHER)


def test_f7_5_minimum_password_length_comes_from_config():
    assert AccountsConfig().min_password_length == 12


def test_f7_5_no_command_or_unknown_command_explains(engine, capsys, owner_id):
    for args in ([], ["delete-everything"]):
        with pytest.raises(SystemExit) as stop:
            main(args, getpass=FakeGetpass(), engine=engine, accounts=FAST)
        assert stop.value.code != 0
    assert "owner" in capsys.readouterr().err


def test_f7_5_database_not_set_up_is_explained(tmp_path, capsys):
    from app.db.session import make_engine

    empty = make_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    code, output, _ = run(empty, capsys, "owner", "--email", "me@example.com")
    assert code != 0
    assert "alembic upgrade head" in output
