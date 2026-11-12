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

from sqlmodel import Session, select

from app.config import ScryptCost
from app.db.models import User

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
