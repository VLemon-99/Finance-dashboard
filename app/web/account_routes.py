"""Logging in and out (SPEC F7.2).

A correct email and password starts a login session (app/services/users.py); its random
token goes in a cookie the page's scripts can't read (HttpOnly), that other websites'
requests don't carry along (SameSite=Lax) and, when the app is reached over HTTPS, that is
only ever sent over HTTPS (Secure). The database keeps only a hash of the token.

Login and logout forms go through the same cross-site check as every other change
(app/main.py), so another website can't sign you in or out behind your back.
"""

import base64
import hashlib
import hmac
import json
import math
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from app.config import AccountsConfig
from app.db.models import User
from app.providers.google_auth import GoogleAuthError
from app.services.users import (
    MAX_EMAIL,
    check_password,
    clear_failed_logins,
    end_session,
    find_by_email,
    hash_password,
    is_valid_email,
    login_locked_until,
    record_failed_login,
    start_session,
    user_for_token,
)
from app.timeutils import as_utc, utc_now
from app.web.routes import SessionDep, _redirect, templates

SESSION_COOKIE = "session"

# Pages that work without being signed in when login is required (F7.4).
OPEN_PATHS = ("/login", "/signup")
OPEN_PREFIXES = ("/login/", "/static/")
MAX_NEXT = 2000

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


def is_open_path(path: str) -> bool:
    """True for the pages anyone may open while login is required."""
    return path in OPEN_PATHS or path.startswith(OPEN_PREFIXES)


def safe_next(value: str | None) -> str:
    """Where to go after logging in: only an address inside this app, else the home page.

    "//evil.example" or "/\\evil.example" would make the browser leave the site, so they
    and anything with a scheme, backslash or control character are refused.
    """
    if not value or len(value) > MAX_NEXT:
        return "/"
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return "/"
    return value


def _accounts(request: Request) -> AccountsConfig:
    return request.app.state.accounts


def _dummy_hash(request: Request) -> str:
    """A hash to check against when the email is unknown, so both cases take as long."""
    state = request.app.state
    if getattr(state, "dummy_password_hash", None) is None:
        state.dummy_password_hash = hash_password("no-such-user", _accounts(request).scrypt)
    return state.dummy_password_hash


def _google_on(request: Request) -> bool:
    google = getattr(request.app.state, "google", None)
    return google is not None and google.is_set_up


def _login_page(
    request: Request, email: str = "", message: str = "", status: int = 200, next_url: str = "/"
):
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "email": email[:MAX_EMAIL],
            "message": message,
            "next": safe_next(next_url),
            "google_on": _google_on(request),
        },
        status_code=status,
    )


def _problem(email: str, password: str) -> str:
    if not email:
        return "Enter your email."
    if not is_valid_email(email):
        return "Check your email address: it should look like name@example.com."
    if not password:
        return "Enter your password."
    if len(password) > MAX_PASSWORD:
        return f"Passwords can be at most {MAX_PASSWORD} characters."
    return ""


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    return _login_page(request, next_url=next)


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    session: SessionDep,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
):
    email = email.strip()
    problem = _problem(email, password)
    if problem:
        return _login_page(request, email, problem, status=400, next_url=next)
    rules = _accounts(request)
    now = utc_now()
    locked_until = login_locked_until(session, email, rules, now)
    if locked_until is not None:  # the password isn't even checked
        minutes = max(1, math.ceil((locked_until - now).total_seconds() / 60))
        wait = "1 minute" if minutes == 1 else f"{minutes} minutes"
        message = (
            f"Too many wrong passwords for this email. Try again in {wait}, "
            "or use Sign in with Google."
        )
        return _login_page(request, email, message, status=429, next_url=next)
    user = session.exec(select(User).where(func.lower(User.email) == email.lower())).first()
    stored = user.password_hash if user is not None else _dummy_hash(request)
    if user is None or not check_password(stored, password):
        record_failed_login(session, email, rules, now)
        return _login_page(request, email, WRONG, status=401, next_url=next)
    clear_failed_logins(session, email)
    return _signed_in_redirect(request, session, user.id, next)


def _signed_in_redirect(request: Request, session: Session, user_id: int, next_url: str):
    """Start a login session for the user, set its cookie and go on to `next_url`."""
    days = _accounts(request).session_days
    token = start_session(session, user_id, days)
    response = _redirect(safe_next(next_url))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=days * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


# --- sign-up (F7.5): invited emails only ------------------------------------------------

NOT_INVITED = "This email is not on the invite list. Ask the owner for an invite."
ALREADY = "An account already uses this email."


def _signup_page(
    request: Request,
    email: str = "",
    message: str = "",
    status: int = 200,
    next_url: str = "/",
    show_login: bool = False,
):
    return templates.TemplateResponse(
        request,
        "signup.html",
        {
            "email": email[:MAX_EMAIL],
            "message": message,
            "next": safe_next(next_url),
            "show_login": show_login,
            "min_length": _accounts(request).min_password_length,
            "google_on": _google_on(request),
        },
        status_code=status,
    )


def _signup_problem(email: str, password: str, again: str, minimum: int) -> str:
    if not email:
        return "Enter your email."
    if not is_valid_email(email):
        return "Check your email address: it should look like name@example.com."
    if len(password) < minimum:
        return f"Passwords must be at least {minimum} characters."
    if len(password) > MAX_PASSWORD:
        return f"Passwords can be at most {MAX_PASSWORD} characters."
    if password != again:
        return "The two passwords do not match."
    return ""


@router.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request, next: str = "/"):
    return _signup_page(request, next_url=next)


@router.post("/signup", response_class=HTMLResponse)
def signup(
    request: Request,
    session: SessionDep,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password_again: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
):
    accounts = _accounts(request)
    email = email.strip()
    problem = _signup_problem(email, password, password_again, accounts.min_password_length)
    if problem:
        return _signup_page(request, email, problem, status=400, next_url=next)
    if not accounts.is_invited(email):
        return _signup_page(request, email, NOT_INVITED, status=403, next_url=next)
    if find_by_email(session, email) is not None:
        return _signup_page(request, email, ALREADY, 409, next, show_login=True)
    user = User(email=email, password_hash=hash_password(password, accounts.scrypt))
    session.add(user)
    try:
        session.commit()
    except IntegrityError:  # the same form sent twice at once
        session.rollback()
        return _signup_page(request, email, ALREADY, 409, next, show_login=True)
    session.refresh(user)
    return _signed_in_redirect(request, session, user.id, next)


@router.post("/logout")
def logout(request: Request, session: SessionDep):
    end_session(session, request.cookies.get(SESSION_COOKIE))
    response = _redirect("/")
    response.delete_cookie(
        SESSION_COOKIE, httponly=True, samesite="lax", secure=request.url.scheme == "https"
    )
    return response


# --- Sign in with Google (F7.6) ---------------------------------------------------------
# While the browser is at Google, the sign-in's state, nonce, code verifier and `next` are
# kept in a short-lived cookie, signed with a random key made when the app starts, so it
# can't be forged or changed (a restart just means signing in again).

PENDING_COOKIE = "google_signin"
PENDING_PATH = "/login/google"
PENDING_MINUTES = 10


def _signing_key(request: Request) -> bytes:
    state = request.app.state
    if getattr(state, "pending_signin_key", None) is None:
        state.pending_signin_key = secrets.token_bytes(32)
    return state.pending_signin_key


def _seal(request: Request, data: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    mac = hmac.new(_signing_key(request), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{mac}"


def _unseal(request: Request, value: str | None) -> dict | None:
    """The pending sign-in, or None if missing, changed or expired."""
    if not value or value.count(".") != 1:
        return None
    body, mac = value.split(".")
    expected = hmac.new(_signing_key(request), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("expires", 0) <= utc_now().timestamp():
        return None
    return data


def _callback_address(request: Request) -> str:
    return str(request.url_for("google_callback"))


def _forget_pending(response, request: Request):
    response.delete_cookie(
        PENDING_COOKIE,
        path=PENDING_PATH,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.get("/login/google")
def google_start(request: Request, next: str = "/"):
    google = request.app.state.google
    if not _google_on(request):
        return _login_page(request, message="Sign in with Google isn't set up.", status=400)
    try:
        start = google.start(_callback_address(request))
    except GoogleAuthError as exc:
        return _login_page(request, message=str(exc), status=400, next_url=next)
    pending = {
        "state": start.state,
        "nonce": start.nonce,
        "verifier": start.code_verifier,
        "next": safe_next(next),
        "expires": (as_utc(utc_now()) + timedelta(minutes=PENDING_MINUTES)).timestamp(),
    }
    response = _redirect(start.url)
    # The verifier and nonce must stay unreadable to anyone but this app: they go in only
    # as part of the signed value, inside an HttpOnly cookie for these two addresses.
    response.set_cookie(
        PENDING_COOKIE,
        _seal(request, pending),
        max_age=PENDING_MINUTES * 60,
        path=PENDING_PATH,
        httponly=True,
        samesite="lax",  # sent when Google sends the browser back (a normal link)
        secure=request.url.scheme == "https",
    )
    return response


@router.get("/login/google/callback", name="google_callback")
def google_callback(
    request: Request,
    session: SessionDep,
    state: str = "",
    code: str = "",
    error: str = "",
):
    pending = _unseal(request, request.cookies.get(PENDING_COOKIE))
    if (
        pending is None
        or not state
        or not hmac.compare_digest(str(pending.get("state", "")), state)
    ):
        page = _login_page(
            request,
            message="That sign-in with Google expired or didn't match. Try again.",
            status=400,
        )
        return _forget_pending(page, request)
    next_url = pending.get("next", "/")
    if error or not code:
        page = _login_page(
            request, message="Sign in with Google was cancelled.", status=400, next_url=next_url
        )
        return _forget_pending(page, request)
    try:
        email = request.app.state.google.finish(
            code, _callback_address(request), pending["nonce"], pending["verifier"]
        )
    except GoogleAuthError as exc:
        return _forget_pending(
            _login_page(request, message=str(exc), status=400, next_url=next_url), request
        )
    user = find_by_email(session, email)
    if user is None:
        if not _accounts(request).is_invited(email):
            page = _login_page(
                request, email, f"{email} {NOT_INVITED[len('This email ') :]}", 403, next_url
            )
            return _forget_pending(page, request)
        user = User(email=email)  # no password: this account signs in with Google
        session.add(user)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            user = find_by_email(session, email)
        if user is None:
            return _forget_pending(_login_page(request, message=ALREADY, status=409), request)
        session.refresh(user)
    response = _signed_in_redirect(request, session, user.id, next_url)
    return _forget_pending(response, request)
