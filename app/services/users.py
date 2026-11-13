"""Users. For now there is one default user, created at startup (F7.1).

Passwords (F7.2) are never stored. We keep a salted hash made with Python's built-in
scrypt, in the form "scrypt$n$r$p$salt$hash" (salt and hash in base64), so a password
can be checked later even after the cost settings in config/accounts.yaml change.
"""

import base64
import binascii
import hashlib
import hmac
import secrets
import threading
from datetime import datetime, timedelta

from sqlmodel import Session, func, select

from app.config import EMAIL_PATTERN, MAX_EMAIL, AccountsConfig, ScryptCost
from app.db.models import LoginFailure, LoginSession, User
from app.timeutils import as_utc, utc_now

_SCHEME = "scrypt"
_SALT_BYTES = 16
_HASH_BYTES = 32


def is_valid_email(email: str) -> bool:
    return 0 < len(email) <= MAX_EMAIL and bool(EMAIL_PATTERN.match(email))


def find_by_email(session: Session, email: str) -> User | None:
    """The user with this email, ignoring case and surrounding spaces."""
    return session.exec(select(User).where(func.lower(User.email) == email.strip().lower())).first()


def ensure_owner(session: Session, default_email: str) -> User:
    """The owner: the first user ever created (the default user, who owns all the data from
    before accounts existed). Created with `default_email` when there are no users yet.

    Found by id, not email, so the owner can rename themselves (app/accounts_cli.py).
    """
    owner = session.exec(select(User).order_by(User.id)).first()
    if owner is None:
        owner = User(email=default_email)
        session.add(owner)
        session.commit()
        session.refresh(owner)
    return owner


def ensure_default_user(session: Session, email: str) -> User:
    """Return the default user, creating it the first time. Safe to call repeatedly."""
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        user = User(email=email)
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


# Each scrypt check uses about 32 MB (with the default cost), so only a few may run at once
# (security review H2, F7.10); the rest wait their turn. Set from config at app start.
_password_slots = threading.BoundedSemaphore(2)


def set_max_parallel_password_checks(limit: int) -> None:
    global _password_slots
    _password_slots = threading.BoundedSemaphore(limit)


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    slots = _password_slots
    with slots:
        return _derive(password, salt, n, r, p)


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=128 * n * r * p + 1024 * 1024,
        dklen=_HASH_BYTES,
    )


def hash_password(password: str, cost: ScryptCost) -> str:
    """Turn a password into the value to store. A new random salt every time."""
    if not password:
        raise ValueError("A password can't be empty.")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _scrypt(password, salt, cost.n, cost.r, cost.p)
    b64 = base64.b64encode
    return f"{_SCHEME}${cost.n}${cost.r}${cost.p}${b64(salt).decode()}${b64(digest).decode()}"


def check_password(stored: str | None, password: str) -> bool:
    """True only if `password` matches the stored hash. No hash, or a damaged one: False."""
    if not stored or not password:
        return False
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != _SCHEME:
        return False
    try:
        cost = ScryptCost(n=int(parts[1]), r=int(parts[2]), p=int(parts[3]))
        salt = base64.b64decode(parts[4], validate=True)
        expected = base64.b64decode(parts[5], validate=True)
    except (ValueError, binascii.Error):  # pydantic's ValidationError is a ValueError
        return False
    if not salt or len(expected) != _HASH_BYTES:
        return False
    actual = _scrypt(password, salt, cost.n, cost.r, cost.p)
    return hmac.compare_digest(actual, expected)  # same time whatever the difference


# --- login sessions (F7.2) -------------------------------------------------------------
# The browser keeps a random token in a cookie; the database keeps only its SHA-256 hash,
# so someone reading the database can't use it to sign in. A plain hash is enough here
# (unlike passwords) because the token is long and random, so it can't be guessed.


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start_session(session: Session, user_id: int, days: int, now: datetime | None = None) -> str:
    """Sign a user in on one device. Returns the token for the cookie (not stored)."""
    if days < 1:
        raise ValueError("A login session lasts at least one day.")
    now = as_utc(now or utc_now())
    token = secrets.token_urlsafe(32)
    session.add(
        LoginSession(
            user_id=user_id,
            token_hash=_token_hash(token),
            created_at=now,
            expires_at=now + timedelta(days=days),
        )
    )
    session.commit()
    return token


def user_for_token(session: Session, token: str | None, now: datetime | None = None) -> User | None:
    """The signed-in user for a cookie's token; None if unknown, ended or expired."""
    if not token:
        return None
    row = session.exec(
        select(LoginSession).where(LoginSession.token_hash == _token_hash(token))
    ).first()
    if row is None or as_utc(row.expires_at) <= as_utc(now or utc_now()):
        return None
    return session.get(User, row.user_id)


def end_session(session: Session, token: str | None) -> None:
    """Log out one device. Unknown or already-ended tokens are ignored."""
    if not token:
        return
    row = session.exec(
        select(LoginSession).where(LoginSession.token_hash == _token_hash(token))
    ).first()
    if row is not None:
        session.delete(row)
        session.commit()


# --- wrong passwords (F7.7) ---------------------------------------------------------------


def _email_key(email: str) -> str:
    return email.strip().lower()


def login_locked_until(
    session: Session, email: str, rules: AccountsConfig, now: datetime
) -> datetime | None:
    """When password logins for this email open again, or None if they aren't locked.

    Locked when the latest wrong try made `max_failed_logins` within the window, until
    `lockout_minutes` after it. Refused attempts aren't recorded, so they don't extend it.
    """
    now = as_utc(now)
    window = timedelta(minutes=rules.failed_login_window_minutes)
    lockout = timedelta(minutes=rules.lockout_minutes)
    times = [
        as_utc(t)
        for t in session.exec(
            select(LoginFailure.failed_at)
            .where(LoginFailure.email == _email_key(email))
            .where(LoginFailure.failed_at > now - window - lockout)
            .order_by(LoginFailure.failed_at.desc())
        ).all()
    ]
    if not times:
        return None
    latest = times[0]
    in_window = [t for t in times if t > latest - window]
    until = latest + lockout
    if len(in_window) >= rules.max_failed_logins and until > now:
        return until
    return None


def record_failed_login(session: Session, email: str, rules: AccountsConfig, now: datetime) -> None:
    """Count one wrong password, and forget tries too old to matter for anyone."""
    now = as_utc(now)
    keep_after = now - timedelta(minutes=rules.failed_login_window_minutes + rules.lockout_minutes)
    for old in session.exec(select(LoginFailure).where(LoginFailure.failed_at <= keep_after)):
        session.delete(old)
    session.add(LoginFailure(email=_email_key(email), failed_at=now))
    session.commit()


def clear_failed_logins(session: Session, email: str) -> None:
    """A successful login starts the count again."""
    for row in session.exec(select(LoginFailure).where(LoginFailure.email == _email_key(email))):
        session.delete(row)
    session.commit()
