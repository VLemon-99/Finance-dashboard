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
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.config import ScryptCost
from app.db.models import LoginSession, User
from app.timeutils import as_utc, utc_now

_SCHEME = "scrypt"
_SALT_BYTES = 16
_HASH_BYTES = 32


def ensure_default_user(session: Session, email: str) -> User:
    """Return the default user, creating it the first time. Safe to call repeatedly."""
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        user = User(email=email)
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
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
