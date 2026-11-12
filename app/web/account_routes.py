"""Logging in and out (SPEC F7.2).

A correct email and password starts a login session (app/services/users.py); its random
token goes in a cookie the page's scripts can't read (HttpOnly), that other websites'
requests don't carry along (SameSite=Lax) and, when the app is reached over HTTPS, that is
only ever sent over HTTPS (Secure). The database keeps only a hash of the token.

Login and logout forms go through the same cross-site check as every other change
(app/main.py), so another website can't sign you in or out behind your back.
"""

import re
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, func, select

from app.config import AccountsConfig
from app.db.models import User
from app.services.users import (
    check_password,
    end_session,
    hash_password,
    start_session,
    user_for_token,
)
from app.web.routes import SessionDep, _redirect, templates

SESSION_COOKIE = "session"

# One "@", something on both sides, no spaces. Deliberately loose: the owner's address
# (and the default "owner@localhost") must pass; the real check is the password.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")
MAX_EMAIL = 320
MAX_PASSWORD = 1024  # longer is refused before hashing, so it can't be used to slow us down

WRONG = "Email or password is wrong."

router = APIRouter()


@dataclass(frozen=True)
class SignedIn:
    """Who the current request's cookie belongs to (set on `request.state.account`)."""

    user_id: int
    email: str


def signed_in(session: Session, token: str | None) -> SignedIn | None:
    """The user a session cookie's token belongs to, or None."""
    user = user_for_token(session, token)
    return None if user is None else SignedIn(user_id=user.id, email=user.email)


def _accounts(request: Request) -> AccountsConfig:
    return request.app.state.accounts


def _dummy_hash(request: Request) -> str:
    """A hash to check against when the email is unknown, so both cases take as long."""
    state = request.app.state
    if getattr(state, "dummy_password_hash", None) is None:
        state.dummy_password_hash = hash_password("no-such-user", _accounts(request).scrypt)
    return state.dummy_password_hash


def _login_page(request: Request, email: str = "", message: str = "", status: int = 200):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"email": email[:MAX_EMAIL], "message": message},
        status_code=status,
    )


def _problem(email: str, password: str) -> str:
    if not email:
        return "Enter your email."
    if len(email) > MAX_EMAIL or not _EMAIL.match(email):
        return "Check your email address: it should look like name@example.com."
    if not password:
        return "Enter your password."
    if len(password) > MAX_PASSWORD:
        return f"Passwords can be at most {MAX_PASSWORD} characters."
    return ""


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return _login_page(request)


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    session: SessionDep,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
):
    email = email.strip()
    problem = _problem(email, password)
    if problem:
        return _login_page(request, email, problem, status=400)
    user = session.exec(select(User).where(func.lower(User.email) == email.lower())).first()
    stored = user.password_hash if user is not None else _dummy_hash(request)
    if user is None or not check_password(stored, password):
        return _login_page(request, email, WRONG, status=401)
    days = _accounts(request).session_days
    token = start_session(session, user.id, days)
    response = _redirect("/")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=days * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.post("/logout")
def logout(request: Request, session: SessionDep):
    end_session(session, request.cookies.get(SESSION_COOKIE))
    response = _redirect("/")
    response.delete_cookie(
        SESSION_COOKIE, httponly=True, samesite="lax", secure=request.url.scheme == "https"
    )
    return response
