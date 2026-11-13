"""F7.6 / F7.5 (story F7-S12): "Sign in with Google" on the login and sign-up pages.

Google itself is replaced by `FakeGoogle` (the adapter's own checks are tested in
tests/test_google_auth.py).
"""

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import AccountsConfig, ScryptCost
from app.db.models import LoginSession, User
from app.main import create_app
from app.providers.google_auth import GoogleAuthError, SignInStart
from app.services.users import hash_password, user_for_token

FAST = ScryptCost(n=2**4, r=1, p=1)
PASSWORD = "correct horse battery"  # noqa: S105 (fake test password)
WRONG = "wrong password"  # noqa: S105 (fake test password)
BUTTON = 'href="/login/google'


class FakeGoogle:
    """Stands in for GoogleSignIn: `email` is what Google says, or `error` is raised."""

    def __init__(self, set_up=True):
        self.is_set_up = set_up
        self.email = "friend@example.com"
        self.error = None
        self.started = []
        self.finished = []

    def start(self, redirect_uri):
        n = len(self.started)
        start = SignInStart(
            url=f"https://accounts.google.com/fake?state=state-{n}",
            state=f"state-{n}",
            nonce=f"nonce-{n}",
            code_verifier=f"verifier-{n}",
        )
        self.started.append((redirect_uri, start))
        return start

    def finish(self, code, redirect_uri, nonce, code_verifier):
        self.finished.append((code, redirect_uri, nonce, code_verifier))
        if self.error:
            raise GoogleAuthError(self.error)
        return self.email


@pytest.fixture
def google():
    return FakeGoogle()


@pytest.fixture
def make_web(engine, fake_market_data, app_config, report_services, fake_fred, fomc, macro_setup):
    clients = []

    def make(google, **accounts):
        app = create_app(
            engine=engine,
            market_data=fake_market_data,
            app_config=app_config,
            report_services=report_services,
            fred=fake_fred,
            fomc=fomc,
            macro=macro_setup,
            accounts=AccountsConfig(scrypt=FAST, invited=["friend@example.com"], **accounts),
            google=google,
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def web(make_web, google):
    return make_web(google)


def users(engine):
    with Session(engine) as s:
        return s.exec(select(User).order_by(User.id)).all()


def begin(web, next_value=None):
    params = {"next": next_value} if next_value is not None else None
    response = web.get("/login/google", params=params, follow_redirects=False)
    assert response.status_code == 303
    return response


def come_back(web, state="state-0", code="fake-code", **extra):
    params = {"state": state, "code": code, **extra}
    return web.get("/login/google/callback", params=params, follow_redirects=False)


def signed_in_user(web, engine):
    with Session(engine) as s:
        return user_for_token(s, web.cookies.get("session"))


# --- the button ---------------------------------------------------------------------


def test_f7_6_button_shows_when_google_is_set_up(web):
    assert BUTTON in web.get("/login").text
    assert BUTTON in web.get("/signup").text


def test_f7_6_no_button_when_google_isnt_set_up(make_web):
    web = make_web(FakeGoogle(set_up=False))
    assert BUTTON not in web.get("/login").text
    assert BUTTON not in web.get("/signup").text
    response = web.get("/login/google", follow_redirects=False)
    assert response.status_code == 400
    assert "set up" in response.text


def test_f7_6_button_carries_next(web):
    assert 'href="/login/google?next=/portfolio"' in web.get("/login?next=/portfolio").text


# --- starting ----------------------------------------------------------------------


def test_f7_6_start_sends_the_browser_to_google(web, google):
    response = begin(web)
    assert response.headers["location"] == "https://accounts.google.com/fake?state=state-0"
    redirect_uri, _ = google.started[0]
    assert redirect_uri == "http://testserver/login/google/callback"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert "Max-Age=600" in cookie
    # The pending sign-in doesn't show its secrets in plain text.
    assert "verifier-0" not in cookie
    assert "nonce-0" not in cookie


# --- coming back --------------------------------------------------------------------


def test_f7_6_existing_user_signs_in_by_email_in_any_case(web, engine, google):
    with Session(engine) as s:
        s.add(User(email="Alice@Example.com", password_hash=hash_password(PASSWORD, FAST)))
        s.commit()
    google.email = "alice@example.com"
    begin(web)
    response = come_back(web)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "session=" in response.headers["set-cookie"]
    assert signed_in_user(web, engine).email == "Alice@Example.com"
    code, redirect_uri, nonce, verifier = google.finished[0]
    assert (code, nonce, verifier) == ("fake-code", "nonce-0", "verifier-0")
    assert redirect_uri == "http://testserver/login/google/callback"
    assert len(users(engine)) == 2  # owner + Alice, nobody new


def test_f7_6_invited_email_without_account_gets_one(web, engine, google):
    begin(web)
    response = come_back(web)
    assert response.status_code == 303
    new = users(engine)[-1]
    assert new.email == "friend@example.com"
    assert new.password_hash is None  # signs in with Google only
    assert signed_in_user(web, engine).id == new.id
    # Next time the same account is used, not a second one.
    web.cookies.clear()
    begin(web)
    come_back(web, state="state-1")
    assert len(users(engine)) == 2


def test_f7_6_not_invited_email_is_refused(web, engine, google):
    google.email = "stranger@example.com"
    begin(web)
    response = come_back(web)
    assert response.status_code == 403
    assert "not on the invite list" in response.text
    assert len(users(engine)) == 1
    assert signed_in_user(web, engine) is None


def test_f7_6_owner_signs_in_with_the_email_set_by_the_command(web, engine, google):
    owner_id = web.app.state.user_id
    with Session(engine) as s:
        owner = s.get(User, owner_id)
        owner.email = "me@example.com"
        s.add(owner)
        s.commit()
    google.email = "me@example.com"
    begin(web)
    come_back(web)
    assert signed_in_user(web, engine).id == owner_id


@pytest.mark.parametrize("state", ["state-9", "", "STATE-0"])
def test_f7_6_wrong_state_is_refused(web, engine, google, state):
    begin(web)
    response = come_back(web, state=state)
    assert response.status_code == 400
    assert google.finished == []
    assert signed_in_user(web, engine) is None


def test_f7_6_callback_without_the_pending_cookie_is_refused(web, engine, google):
    response = come_back(web)
    assert response.status_code == 400
    assert google.finished == []
    assert len(users(engine)) == 1


def test_f7_6_tampered_pending_cookie_is_refused(web, engine, google):
    begin(web)
    value = web.cookies.get("google_signin")
    web.cookies.set("google_signin", "x" + value[1:], path="/login/google")
    response = come_back(web)
    assert response.status_code == 400
    assert google.finished == []


def test_f7_6_pending_sign_in_works_once(web, engine, google):
    begin(web)
    assert come_back(web).status_code == 303
    web.cookies.clear()
    assert come_back(web).status_code == 400


def test_f7_6_user_cancelling_at_google_is_explained(web, engine, google):
    begin(web)
    response = come_back(web, code="", error="access_denied")
    assert response.status_code == 400
    assert "cancelled" in response.text
    assert google.finished == []


def test_f7_6_adapter_errors_show_on_the_login_page(web, engine, google):
    google.error = "Google hasn't verified this account's email address."
    begin(web)
    response = come_back(web)
    assert response.status_code == 400
    assert "verified this account" in response.text
    assert "<form" in response.text  # the login page, nothing broke
    assert signed_in_user(web, engine) is None


@pytest.mark.parametrize(
    "next_value, lands", [("/portfolio?x=1", "/portfolio?x=1"), ("//evil.example", "/")]
)
def test_f7_6_after_sign_in_user_lands_on_safe_next(web, google, next_value, lands):
    begin(web, next_value)
    assert come_back(web).headers["location"] == lands


def test_f7_6_works_while_password_logins_are_locked(make_web, engine, google):
    web = make_web(google, max_failed_logins=2)
    with Session(engine) as s:
        s.add(User(email="friend@example.com", password_hash=hash_password(PASSWORD, FAST)))
        s.commit()
    for _ in range(2):
        web.post("/login", data={"email": "friend@example.com", "password": WRONG})
    locked = web.post("/login", data={"email": "friend@example.com", "password": PASSWORD})
    assert locked.status_code == 429
    begin(web)
    assert come_back(web).status_code == 303
    assert signed_in_user(web, engine).email == "friend@example.com"


def test_f7_6_works_when_login_is_required(make_web, engine, google):
    web = make_web(google, login_required=True)
    begin(web)
    assert come_back(web).status_code == 303
    assert web.get("/", follow_redirects=False).status_code == 200


def test_f7_6_the_redirect_address_follows_https(make_web, engine, google):
    web = make_web(google)
    https = TestClient(web.app, base_url="https://testserver")
    response = https.get("/login/google", follow_redirects=False)
    assert "Secure" in response.headers["set-cookie"]
    assert google.started[-1][0] == "https://testserver/login/google/callback"


def test_f7_6_sessions_only_for_successful_sign_ins(web, engine, google):
    google.email = "stranger@example.com"
    begin(web)
    come_back(web)
    with Session(engine) as s:
        assert s.exec(select(LoginSession)).all() == []


def test_f7_6_start_location_is_googles(web):
    location = begin(web).headers["location"]
    parts = urlsplit(location)
    assert parts.netloc == "accounts.google.com"
    assert parse_qs(parts.query)["state"] == ["state-0"]
