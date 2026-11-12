"""F7.4 (story F7-S5): logging in is required only when `login_required` is on (online)."""

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import AccountsConfig, ScryptCost
from app.db.models import User, Watchlist
from app.main import create_app
from app.services.users import hash_password
from app.web.account_routes import safe_next

FAST = ScryptCost(n=2**4, r=1, p=1)
EMAIL = "alice@example.com"
PASSWORD = "correct horse battery"  # noqa: S105 (fake test password)


@pytest.fixture
def make_client(
    engine,
    fake_market_data,
    app_config,
    report_services,
    report_runner,
    fake_fred,
    fomc,
    macro_setup,
):
    clients = []

    def make(**accounts):
        app = create_app(
            engine=engine,
            market_data=fake_market_data,
            app_config=app_config,
            report_services=report_services,
            report_runner=report_runner,
            fred=fake_fred,
            fomc=fomc,
            macro=macro_setup,
            accounts=AccountsConfig(**accounts),
        )
        client = TestClient(app)
        client.__enter__()  # runs startup (creates the default user)
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def online(make_client, engine):
    client = make_client(login_required=True)
    with Session(engine) as s:
        s.add(User(email=EMAIL, password_hash=hash_password(PASSWORD, FAST)))
        s.commit()
    return client


def _login(client, next_value=None):
    data = {"email": EMAIL, "password": PASSWORD}
    if next_value is not None:
        data["next"] = next_value
    return client.post("/login", data=data, follow_redirects=False)


def _next_of(response):
    location = response.headers["location"]
    parts = urlsplit(location)
    assert parts.path == "/login"
    return parse_qs(parts.query).get("next", [None])[0]


def test_f7_4_login_not_required_by_default():
    assert AccountsConfig().login_required is False


def test_f7_4_real_accounts_config_has_login_off():
    from app.config import PROJECT_ROOT, load_accounts_config

    assert load_accounts_config(PROJECT_ROOT / "config" / "accounts.yaml").login_required is False


def test_f7_4_off_pages_work_without_a_cookie(make_client):
    client = make_client()
    for path in ("/", "/portfolio", "/reports", "/calendar", "/options", "/lookup"):
        assert client.get(path, follow_redirects=False).status_code == 200, path


@pytest.mark.parametrize(
    "path",
    ["/", "/portfolio", "/reports", "/calendar", "/macro", "/lookup", "/watchlists/1"],
)
def test_f7_4_on_pages_send_visitors_to_login(online, path):
    response = online.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert _next_of(response) == path


def test_f7_4_on_next_keeps_the_query(online):
    response = online.get("/reports?ticker=AAPL&day=2026-11-02", follow_redirects=False)
    assert _next_of(response) == "/reports?ticker=AAPL&day=2026-11-02"


def test_f7_4_on_actions_without_login_change_nothing(online, engine):
    response = online.post("/watchlists", data={"name": "Sneaky"}, follow_redirects=False)
    assert response.status_code == 303
    assert urlsplit(response.headers["location"]).path == "/login"
    with Session(engine) as s:
        assert s.exec(select(Watchlist)).all() == []


def test_f7_4_on_login_page_and_static_files_work(online):
    assert online.get("/login").status_code == 200
    assert online.get("/static/style.css").status_code == 200
    assert online.get("/signup", follow_redirects=False).status_code != 303
    assert (
        online.get("/login/google", follow_redirects=False)
        .headers.get("location", "")
        .split("?")[0]
        != "/login"
    )


def test_f7_4_on_signed_in_user_sees_pages(online):
    assert _login(online).status_code == 303
    assert online.get("/", follow_redirects=False).status_code == 200
    assert online.get("/portfolio", follow_redirects=False).status_code == 200


def test_f7_4_login_page_carries_next_into_the_form(online):
    page = online.get("/login", params={"next": "/portfolio?x=1"}).text
    assert 'name="next"' in page
    assert 'value="/portfolio?x=1"' in page


def test_f7_4_after_login_user_goes_back_to_safe_next(online):
    response = _login(online, "/portfolio?x=1")
    assert response.headers["location"] == "/portfolio?x=1"


@pytest.mark.parametrize(
    "bad",
    [
        "//evil.example",
        "https://evil.example",
        "http://evil.example/x",
        "/\\evil.example",
        "\\\\evil.example",
        "",
        "portfolio",
        "javascript:alert(1)",
        "/ok\r\nSet-Cookie: x=1",
    ],
)
def test_f7_4_unsafe_next_lands_on_home(online, bad):
    response = _login(online, bad)
    assert response.headers["location"] == "/"


def test_f7_4_safe_next_helper():
    assert safe_next("/portfolio") == "/portfolio"
    assert safe_next("/reports?ticker=AAPL") == "/reports?ticker=AAPL"
    assert safe_next(None) == "/"
    assert safe_next("/" + "a" * 3000) == "/"  # absurdly long addresses aren't kept


def test_f7_4_on_failed_login_keeps_next(online):
    response = online.post(
        "/login",
        data={"email": EMAIL, "password": "wrong password", "next": "/portfolio"},  # noqa: S106
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert 'value="/portfolio"' in response.text


def test_f7_4_on_bad_or_expired_cookie_counts_as_not_signed_in(online):
    online.cookies.set("session", "made-up-token")
    response = online.get("/portfolio", follow_redirects=False)
    assert response.status_code == 303
    assert _next_of(response) == "/portfolio"


def test_f7_4_on_htmx_requests_get_an_hx_redirect(online):
    response = online.get(
        "/lookup/result",
        params={"ticker": "AAPL"},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == "/login"
    assert "<html" not in response.text


def test_f7_4_on_logout_then_pages_need_login_again(online):
    _login(online)
    online.post("/logout")
    response = online.get("/", follow_redirects=False)
    assert response.status_code == 303
