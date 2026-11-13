"""F7.10 (story F7-S18, security review M1): setting a new password with the account command
signs out every device of that account."""

import pytest
from sqlmodel import Session

from app.accounts_cli import main
from app.config import AccountsConfig, ScryptCost
from app.db.models import User
from app.services.users import end_all_sessions, ensure_owner, start_session, user_for_token

FAST = AccountsConfig(scrypt=ScryptCost(n=2**4, r=1, p=1))
GOOD = "a long enough password"  # noqa: S105 (fake test password)


def run(engine, capsys, *args, answers=(GOOD, GOOD)):
    answers = list(answers)
    code = main(list(args), getpass=lambda prompt="": answers.pop(0), engine=engine, accounts=FAST)
    out = capsys.readouterr()
    return code, out.out + out.err


@pytest.fixture
def people(engine):
    with Session(engine) as s:
        owner = ensure_owner(s, "owner@localhost")
        friend = User(email="friend@example.com")
        other = User(email="other@example.com")
        s.add_all([friend, other])
        s.commit()
        tokens = {
            "owner": [start_session(s, owner.id, days=30) for _ in range(2)],
            "friend": [start_session(s, friend.id, days=30) for _ in range(3)],
            "other": [start_session(s, other.id, days=30)],
        }
        return {"owner": owner.id, "friend": friend.id, "other": other.id, "tokens": tokens}


def works(engine, token):
    with Session(engine) as s:
        return user_for_token(s, token) is not None


def test_f7_10_password_command_signs_out_that_accounts_devices(engine, capsys, people):
    code, output = run(engine, capsys, "password", "--email", "friend@example.com")
    assert code == 0
    assert "signed out" in output
    assert not any(works(engine, t) for t in people["tokens"]["friend"])
    assert all(works(engine, t) for t in people["tokens"]["other"])
    assert all(works(engine, t) for t in people["tokens"]["owner"])


def test_f7_10_owner_command_signs_out_the_owners_devices(engine, capsys, people):
    code, output = run(engine, capsys, "owner", "--email", "me@example.com")
    assert code == 0
    assert "signed out" in output
    assert not any(works(engine, t) for t in people["tokens"]["owner"])
    assert all(works(engine, t) for t in people["tokens"]["friend"])


@pytest.mark.parametrize(
    "args, answers",
    [
        (("password", "--email", "friend@example.com"), ("short", "short")),
        (("password", "--email", "friend@example.com"), (GOOD, GOOD + "x")),
        (("password", "--email", "nobody@example.com"), (GOOD, GOOD)),
        (("owner", "--email", "friend@example.com"), (GOOD, GOOD)),  # email taken
    ],
)
def test_f7_10_refused_commands_sign_out_no_one(engine, capsys, people, args, answers):
    code, _ = run(engine, capsys, *args, answers=answers)
    assert code != 0
    for tokens in people["tokens"].values():
        assert all(works(engine, t) for t in tokens)


def test_f7_10_end_all_sessions_only_touches_that_user(engine, people):
    with Session(engine) as s:
        ended = end_all_sessions(s, people["friend"])
    assert ended == 3
    assert all(works(engine, t) for t in people["tokens"]["other"])
