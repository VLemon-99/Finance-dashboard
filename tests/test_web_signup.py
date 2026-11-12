"""F7.5 (story F7-S7): invite-only sign-up with email and password."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import AccountsConfig, ConfigFileError, ScryptCost, load_accounts_config
from app.db.models import LoginSession, User, Watchlist
from app.main import create_app
from app.services.users import check_password, user_for_token

FAST = ScryptCost(n=2**4, r=1, p=1)
INVITED = "friend@example.com"
GOOD = "a long enough password"  # noqa: S105 (fake test password)


@pytest.fixture
def web(
    engine,
    fake_market_data,
    app_config,
    report_services,
    report_runner,
    fake_fred,
    fomc,
    macro_setup,
):
    app = create_app(
        engine=engine,
        market_data=fake_market_data,
        app_config=app_config,
        report_services=report_services,
        report_runner=report_runner,
        fred=fake_fred,
        fomc=fomc,
        macro=macro_setup,
        accounts=AccountsConfig(scrypt=FAST, invited=[" Friend@Example.com ", "other@example.com"]),
    )
    with TestClient(app) as client:
        yield client


def signup(web, email=INVITED, password=GOOD, again=None, headers=None, **extra):
    data = {"email": email, "password": password, "password_again": again or password, **extra}
    return web.post("/signup", data=data, headers=headers, follow_redirects=False)


def all_users(engine):
    with Session(engine) as s:
        return s.exec(select(User).order_by(User.id)).all()


def sessions(engine):
    with Session(engine) as s:
        return s.exec(select(LoginSession)).all()


def test_f7_5_signup_page_shows_a_form(web):
    page = web.get("/signup")
    assert page.status_code == 200
    for name in ('name="email"', 'name="password"', 'name="password_again"'):
        assert name in page.text


def test_f7_5_invited_email_creates_one_account_and_signs_in(web, engine):
    response = signup(web)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    users = all_users(engine)
    assert len(users) == 2  # the owner + the new user
    new = users[1]
    assert new.email == INVITED
    assert new.password_hash and GOOD not in new.password_hash
    assert check_password(new.password_hash, GOOD)
    with Session(engine) as s:
        assert user_for_token(s, web.cookies.get("session")).id == new.id


def test_f7_5_new_user_starts_empty_and_sees_none_of_the_owners_data(web, engine):
    owner_id = web.app.state.user_id
    with Session(engine) as s:
        s.add(Watchlist(user_id=owner_id, name="Owner Picks"))
        s.commit()
    signup(web)
    home = web.get("/").text
    assert "Owner Picks" not in home
    assert "No watchlists yet" in home
    assert INVITED in home  # signed in as the new user


def test_f7_5_invite_list_ignores_case_and_spaces(web, engine):
    response = signup(web, email="  FRIEND@example.COM ")
    assert response.status_code == 303
    assert all_users(engine)[1].email == "FRIEND@example.COM"


def test_f7_5_email_not_invited_is_refused(web, engine):
    response = signup(web, email="stranger@example.com")
    assert response.status_code == 403
    assert "not on the invite list" in response.text
    assert len(all_users(engine)) == 1
    assert "set-cookie" not in response.headers


def test_f7_5_invited_email_with_an_account_points_to_login(web, engine):
    signup(web)
    web.cookies.clear()
    response = signup(web, email="friend@EXAMPLE.com", password="a different long one")  # noqa: S106
    assert response.status_code == 409
    assert "already" in response.text
    assert 'href="/login"' in response.text
    assert len(all_users(engine)) == 2
    assert check_password(all_users(engine)[1].password_hash, GOOD)  # unchanged


@pytest.mark.parametrize(
    "email, password, again, message",
    [
        (INVITED, "too short", "too short", "at least 12 characters"),
        (INVITED, GOOD, GOOD + "x", "do not match"),
        (INVITED, "", "", "at least 12 characters"),
        ("", GOOD, GOOD, "Enter your email"),
        ("not-an-email", GOOD, GOOD, "should look like name@example.com"),
        (INVITED, "x" * 1025, "x" * 1025, "at most 1024"),
    ],
)
def test_f7_5_bad_input_explains_and_creates_nothing(web, engine, email, password, again, message):
    response = signup(web, email=email, password=password, again=again)
    assert response.status_code == 400
    assert message in response.text
    assert len(all_users(engine)) == 1
    assert sessions(engine) == []
    assert "set-cookie" not in response.headers
    if password:
        assert password not in response.text


def test_f7_5_failed_signup_keeps_the_email(web):
    response = signup(web, password="too short", again="too short")  # noqa: S106
    assert f'value="{INVITED}"' in response.text


def test_f7_5_signup_goes_to_a_safe_next(web):
    assert signup(web, next="/portfolio")
    web.cookies.clear()
    response = signup(web, email="other@example.com", next="//evil.example")
    assert response.headers["location"] == "/"


def test_f7_5_signup_goes_to_next_when_given(web):
    response = signup(web, next="/portfolio")
    assert response.headers["location"] == "/portfolio"


def test_f7_5_signup_from_another_website_is_refused(web, engine):
    response = signup(web, headers={"origin": "http://evil.example"})
    assert response.status_code == 403
    assert len(all_users(engine)) == 1


def test_f7_5_login_page_links_to_signup(web):
    assert 'href="/signup' in web.get("/login").text


def test_f7_5_signup_works_when_login_is_required(
    engine, fake_market_data, app_config, report_services, fake_fred, fomc, macro_setup
):
    app = create_app(
        engine=engine,
        market_data=fake_market_data,
        app_config=app_config,
        report_services=report_services,
        fred=fake_fred,
        fomc=fomc,
        macro=macro_setup,
        accounts=AccountsConfig(scrypt=FAST, invited=[INVITED], login_required=True),
    )
    with TestClient(app) as client:
        assert client.get("/signup").status_code == 200
        assert signup(client).status_code == 303
        assert client.get("/", follow_redirects=False).status_code == 200


# --- config/accounts.yaml -----------------------------------------------------------


def test_f7_5_invite_list_ships_empty():
    from app.config import PROJECT_ROOT

    assert load_accounts_config(PROJECT_ROOT / "config" / "accounts.yaml").invited == []
    assert AccountsConfig().invited == []


def test_f7_5_invite_list_is_cleaned_and_checked(tmp_path):
    path = tmp_path / "accounts.yaml"
    path.write_text("invited:\n  - ' A@Example.com '\n  - a@example.com\n", encoding="utf-8")
    loaded = load_accounts_config(path)
    assert loaded.invited == ["a@example.com"]
    assert loaded.is_invited("A@EXAMPLE.COM ")
    assert not loaded.is_invited("b@example.com")
    for bad in ("invited:\n  - not-an-email\n", "invited:\n  - 'a b@example.com'\n"):
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(ConfigFileError, match="accounts.yaml"):
            load_accounts_config(path)
