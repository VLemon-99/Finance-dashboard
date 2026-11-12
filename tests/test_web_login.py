"""F7.2 (story F7-S3): log in and log out with email and password."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import ScryptCost
from app.db.models import LoginSession, User
from app.main import create_app
from app.services.users import hash_password, user_for_token

FAST = ScryptCost(n=2**4, r=1, p=1)
EMAIL = "alice@example.com"
PASSWORD = "correct horse battery"  # noqa: S105 (fake test password)
COOKIE = "session"
WRONG = "Email or password is wrong"


@pytest.fixture
def alice(engine):
    with Session(engine) as s:
        user = User(email=EMAIL, password_hash=hash_password(PASSWORD, FAST))
        s.add(user)
        s.commit()
        s.refresh(user)
        return user.id


def _login(client, email=EMAIL, password=PASSWORD, **headers):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        headers=headers,
        follow_redirects=False,
    )


def _sessions(engine):
    with Session(engine) as s:
        return s.exec(select(LoginSession)).all()


def test_f7_2_login_page_shows_a_form(client):
    page = client.get("/login")
    assert page.status_code == 200
    assert 'name="email"' in page.text
    assert 'name="password"' in page.text
    assert 'type="password"' in page.text


def test_f7_2_right_password_redirects_home_with_a_safe_cookie(client, engine, alice):
    response = _login(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie or "SameSite=Lax" in cookie
    assert "Secure" not in cookie  # plain http at localhost
    token = client.cookies.get(COOKIE)
    assert token
    with Session(engine) as s:
        assert user_for_token(s, token).id == alice


def test_f7_2_email_is_trimmed_and_case_insensitive(client, alice):
    response = _login(client, email="  Alice@Example.COM ")
    assert response.status_code == 303
    assert client.cookies.get(COOKIE)


def test_f7_2_cookie_is_secure_over_https(engine, alice, client):
    app = client.app
    with TestClient(app, base_url="https://testserver") as https_client:
        response = _login(https_client)
    assert response.status_code == 303
    assert "Secure" in response.headers["set-cookie"]


@pytest.mark.parametrize(
    "email, password",
    [(EMAIL, "wrong password"), ("nobody@example.com", PASSWORD), (EMAIL, PASSWORD.upper())],
)
def test_f7_2_wrong_password_or_unknown_email_same_message_no_cookie(
    client, engine, alice, email, password
):
    response = _login(client, email=email, password=password)
    assert response.status_code == 401
    assert WRONG in response.text
    assert "set-cookie" not in response.headers
    assert _sessions(engine) == []


def test_f7_2_user_without_password_cannot_log_in(client, engine):
    with Session(engine) as s:
        s.add(User(email="nopass@example.com"))
        s.commit()
    response = _login(client, email="nopass@example.com", password="anything")  # noqa: S106
    assert response.status_code == 401
    assert WRONG in response.text
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "email, password, message",
    [
        ("", PASSWORD, "Enter your email"),
        ("   ", PASSWORD, "Enter your email"),
        ("not-an-email", PASSWORD, "should look like name@example.com"),
        ("two@@example.com", PASSWORD, "should look like name@example.com"),
        ("a b@example.com", PASSWORD, "should look like name@example.com"),
        ("x" * 321 + "@example.com", PASSWORD, "should look like name@example.com"),
        (EMAIL, "", "Enter your password"),
    ],
)
def test_f7_2_empty_or_malformed_input_explains_and_sets_no_cookie(
    client, engine, alice, email, password, message
):
    response = _login(client, email=email, password=password)
    assert response.status_code == 400
    assert message in response.text
    assert "set-cookie" not in response.headers
    assert _sessions(engine) == []


def test_f7_2_missing_form_fields_are_handled(client, alice):
    response = client.post("/login", data={}, follow_redirects=False)
    assert response.status_code == 400
    assert "Enter your email" in response.text


def test_f7_2_failed_login_keeps_the_typed_email_but_not_the_password(client, alice):
    response = _login(client, password="wrong password")  # noqa: S106
    assert f'value="{EMAIL}"' in response.text
    assert "wrong password" not in response.text


def test_f7_2_logout_clears_cookie_and_ends_the_session(client, engine, alice):
    _login(client)
    token = client.cookies.get(COOKIE)
    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f'{COOKIE}=""') or "Max-Age=0" in cookie
    assert not client.cookies.get(COOKIE)
    with Session(engine) as s:
        assert user_for_token(s, token) is None
    # The old token, sent again, identifies no one.
    client.cookies.set(COOKIE, token)
    assert EMAIL not in client.get("/").text


def test_f7_2_logout_without_being_signed_in_is_harmless(client):
    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 303


def test_f7_2_login_posted_from_another_website_is_refused(client, engine, alice):
    response = _login(client, origin="http://evil.example")
    assert response.status_code == 403
    assert "set-cookie" not in response.headers
    assert _sessions(engine) == []


def test_f7_2_logout_posted_from_another_website_is_refused(client, engine, alice):
    _login(client)
    response = client.post(
        "/logout", headers={"origin": "http://evil.example"}, follow_redirects=False
    )
    assert response.status_code == 403
    assert len(_sessions(engine)) == 1


def test_f7_2_menu_shows_email_and_logout_when_signed_in(client, alice):
    _login(client)
    for path in ("/", "/portfolio", "/reports", "/login"):
        page = client.get(path).text
        assert EMAIL in page
        assert 'action="/logout"' in page
        assert "Log out" in page
        assert 'href="/login"' not in page


def test_f7_2_menu_shows_login_link_when_not_signed_in(client, alice):
    page = client.get("/").text
    assert 'href="/login"' in page
    assert 'action="/logout"' not in page
    assert EMAIL not in page


def test_f7_2_unknown_cookie_shows_login_link(client, alice):
    client.cookies.set(COOKIE, "made-up-token")
    page = client.get("/").text
    assert 'href="/login"' in page
    assert EMAIL not in page


def test_f7_2_email_in_menu_is_escaped(client, engine):
    with Session(engine) as s:
        s.add(User(email="<b>x</b>@example.com", password_hash=hash_password(PASSWORD, FAST)))
        s.commit()
    # Such an email can't be typed into the form (it has no spaces, so it passes the format
    # check), and the menu must show it as text, not as HTML.
    _login(client, email="<b>x</b>@example.com")
    page = client.get("/").text
    assert "<b>x</b>@example.com" not in page
    assert "&lt;b&gt;x&lt;/b&gt;@example.com" in page


def test_f7_2_session_length_comes_from_accounts_config(
    engine, fake_market_data, app_config, report_services, fake_fred, fomc, macro_setup, alice
):
    from datetime import UTC

    from app.config import AccountsConfig

    app = create_app(
        engine=engine,
        market_data=fake_market_data,
        app_config=app_config,
        report_services=report_services,
        fred=fake_fred,
        fomc=fomc,
        macro=macro_setup,
        accounts=AccountsConfig(session_days=3),
    )
    with TestClient(app) as c:
        response = _login(c)
    assert "Max-Age=259200" in response.headers["set-cookie"]  # 3 days in seconds
    (row,) = _sessions(engine)
    lasts = row.expires_at.replace(tzinfo=UTC) - row.created_at.replace(tzinfo=UTC)
    assert lasts.days == 3
