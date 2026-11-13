"""Account commands, run on the computer or server where the app's database lives (F7.5).

    uv run python -m app.accounts_cli owner --email you@example.com
        Give your account (the owner, who owns all your existing data) your real email and
        a password. Run it again to change either.

    uv run python -m app.accounts_cli password --email someone@example.com
        Set a new password for an existing account (how a forgotten password is reset).

The password is asked for twice and never shown, printed or logged.
"""

import argparse
import getpass as getpass_module
import sys
from collections.abc import Callable, Sequence

from sqlalchemy import Engine
from sqlmodel import Session

from app.config import AccountsConfig, get_settings, load_accounts_config
from app.db.session import database_is_set_up, database_is_up_to_date, make_engine
from app.services.users import (
    end_all_sessions,
    ensure_owner,
    find_by_email,
    hash_password,
    is_valid_email,
)


class Refused(Exception):
    """A plain message for the person running the command; nothing was changed."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uv run python -m app.accounts_cli",
        description="Set the owner's email and password, or reset a password.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    owner = commands.add_parser("owner", help="set your (the owner's) email and password")
    owner.add_argument("--email", required=True)
    password = commands.add_parser("password", help="set a new password for an account")
    password.add_argument("--email", required=True)
    return parser


def _ask_password(getpass: Callable[[str], str], minimum: int) -> str:
    first = getpass("New password: ")
    second = getpass("Same password again: ")
    if len(first) < minimum:
        raise Refused(f"Passwords must be at least {minimum} characters. Nothing was changed.")
    if first != second:
        raise Refused("The two passwords didn't match. Nothing was changed.")
    return first


def _clean_email(raw: str) -> str:
    email = raw.strip()
    if not is_valid_email(email):
        raise Refused(
            "That doesn't look like an email address (like name@example.com). Nothing was changed."
        )
    return email


def _set_owner(session: Session, email: str, password_hash: str, default_email: str) -> str:
    owner = ensure_owner(session, default_email)
    taken = find_by_email(session, email)
    if taken is not None and taken.id != owner.id:
        raise Refused(f"{email} already belongs to another account. Nothing was changed.")
    owner.email = email
    owner.password_hash = password_hash
    session.add(owner)
    session.commit()
    ended = end_all_sessions(session, owner.id)  # security review M1 (F7.10)
    return f"Done: the owner account is now {email}, with the new password. {_signed_out(ended)}"


def _set_password(session: Session, email: str, password_hash: str) -> str:
    user = find_by_email(session, email)
    if user is None:
        raise Refused(f"No account uses {email}. Nothing was changed.")
    user.password_hash = password_hash
    session.add(user)
    session.commit()
    ended = end_all_sessions(session, user.id)  # security review M1 (F7.10)
    return f"Done: new password set for {user.email}. {_signed_out(ended)}"


def _signed_out(count: int) -> str:
    devices = "device was" if count == 1 else "devices were"
    return f"{count} logged-in {devices} signed out."


def main(
    argv: Sequence[str] | None = None,
    getpass: Callable[[str], str] = getpass_module.getpass,
    engine: Engine | None = None,
    accounts: AccountsConfig | None = None,
) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    accounts = accounts or load_accounts_config(settings.accounts_config_file)
    engine = engine or make_engine(settings.database_url)
    try:
        if not database_is_set_up(engine) or not database_is_up_to_date(engine):
            raise Refused("The database needs setting up first. Run: uv run alembic upgrade head")
        email = _clean_email(args.email)
        if args.command == "password":
            with Session(engine) as session:  # check before asking for a password
                if find_by_email(session, email) is None:
                    raise Refused(f"No account uses {email}. Nothing was changed.")
        password_hash = hash_password(
            _ask_password(getpass, accounts.min_password_length), accounts.scrypt
        )
        with Session(engine) as session:
            if args.command == "owner":
                message = _set_owner(session, email, password_hash, settings.default_user_email)
            else:
                message = _set_password(session, email, password_hash)
    except Refused as exc:
        print(exc, file=sys.stderr)
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
