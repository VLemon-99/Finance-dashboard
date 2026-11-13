"""IMP-8: the app frame on every page (main menu, current page, phone tab bar, palette)."""

import re

import pytest
from sqlmodel import Session

from app.config import ScryptCost
from app.db.models import User
from app.services.users import hash_password
from app.web.assets import STATIC_DIR
from tests.conftest import OPTION_EXPIRATION

SECTIONS = ["Watchlists", "Portfolio", "Options", "Reports", "Calendar", "Macro", "Quote lookup"]
NAV = re.compile(r'<nav class="main-nav"[^>]*>(.*?)</nav>', re.S)
LINK = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S)


def main_nav(html: str) -> str:
    navs = NAV.findall(html)
    assert len(navs) == 1, "expected exactly one main menu"
    return navs[0]


def link_text(inner: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", inner).split())


def current_sections(html: str) -> list[str]:
    """Labels of the menu items marked as the current page. The mark sits on the list item
    around the link, because existing tests pin the link itself to <a href="/x">Label</a>."""
    items = re.findall(r"<li\b([^>]*)>\s*<a\b[^>]*>(.*?)</a>", main_nav(html), re.S)
    marked = [link_text(inner) for attrs, inner in items if 'aria-current="page"' in attrs]
    assert 'aria-current="page"' not in "".join(a for a, _ in LINK.findall(main_nav(html)))
    return marked


@pytest.fixture
def web(client, options_clock):
    client.post("/watchlists", data={"name": "Tech"})
    return client


@pytest.mark.parametrize(
    ("path", "section"),
    [
        ("/", "Watchlists"),
        ("/watchlists/1", "Watchlists"),
        ("/options", "Options"),
        (f"/options/AAPL?expiration={OPTION_EXPIRATION.isoformat()}", "Options"),
        ("/portfolio", "Portfolio"),
        ("/portfolio/trade?symbol=AAPL&side=bought", "Portfolio"),
        ("/reports", "Reports"),
        ("/calendar", "Calendar"),
        ("/macro", "Macro"),
        ("/lookup", "Quote lookup"),
        ("/lookup?ticker=AAPL", "Quote lookup"),
    ],
)
def test_imp8_current_section_is_marked_in_the_main_menu(web, path, section):
    response = web.get(path)
    assert response.status_code == 200
    assert current_sections(response.text) == [section]


@pytest.mark.parametrize("path", ["/login", "/watchlists/999"])
def test_imp8_login_and_not_found_pages_mark_no_section(web, path):
    assert current_sections(web.get(path).text) == []


@pytest.mark.parametrize("path", ["/", "/portfolio", "/calendar", "/login", "/watchlists/999"])
def test_imp8_menu_lists_every_section(web, path):
    nav = main_nav(web.get(path).text)
    texts = [link_text(inner) for _, inner in LINK.findall(nav)]
    for section in SECTIONS:
        assert section in texts, f"{section} missing from the menu on {path}"


def test_imp8_more_menu_works_without_javascript(web):
    nav = main_nav(web.get("/").text)
    more = re.search(
        r"<details\b[^>]*>\s*<summary\b[^>]*>(.*?)</summary>(.*?)</details>", nav, re.S
    )
    assert more, "the less-used sections sit in a <details> 'More' menu"
    assert "More" in link_text(more.group(1))
    inside = [link_text(inner) for _, inner in LINK.findall(more.group(2))]
    assert "Macro" in inside and "Quote lookup" in inside


def test_imp8_more_is_highlighted_when_the_current_page_is_inside_it(web):
    nav = main_nav(web.get("/macro").text)
    summary = re.search(r"<summary\b([^>]*)>", nav).group(1)
    assert "is-current" in summary
    nav = main_nav(web.get("/").text)
    assert "is-current" not in re.search(r"<summary\b([^>]*)>", nav).group(1)


def test_imp8_login_link_is_in_the_menu_when_signed_out(web):
    assert 'href="/login"' in main_nav(web.get("/").text)


def test_imp8_account_and_logout_are_in_the_menu_when_signed_in(web, engine):
    email, password = "pat@example.com", "a long fake password"  # noqa: S105 (fake)
    with Session(engine) as s:
        s.add(User(email=email, password_hash=hash_password(password, ScryptCost(n=16, r=1, p=1))))
        s.commit()
    web.post("/login", data={"email": email, "password": password}, follow_redirects=False)
    nav = main_nav(web.get("/portfolio").text)
    assert email in nav
    assert 'action="/logout"' in nav


def test_imp8_account_email_in_the_menu_is_escaped(web, engine):
    email, password = "<i>x</i>@example.com", "a long fake password"  # noqa: S105 (fake)
    with Session(engine) as s:
        s.add(User(email=email, password_hash=hash_password(password, ScryptCost(n=16, r=1, p=1))))
        s.commit()
    web.post("/login", data={"email": email, "password": password}, follow_redirects=False)
    nav = main_nav(web.get("/").text)
    assert "<i>x</i>" not in nav
    assert "&lt;i&gt;x&lt;/i&gt;@example.com" in nav


def test_imp8_page_uses_the_full_phone_screen():
    base = (STATIC_DIR.parent / "templates" / "base.html").read_text()
    assert "viewport-fit=cover" in base


# --- The stylesheet ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def css():
    return (STATIC_DIR / "style.css").read_text()


def test_imp8_colour_names_are_kept_for_light_and_dark(css):
    light = css.split("@media (prefers-color-scheme: dark)")[0]
    dark = css.split("@media (prefers-color-scheme: dark)")[1]
    for name in (
        "--bg",
        "--card",
        "--text",
        "--muted",
        "--border",
        "--up",
        "--down",
        "--warn-bg",
        "--warn",
        "--err-bg",
        "--itm-bg",
        "--accent",
    ):
        assert f"{name}:" in light, name
        assert f"{name}:" in dark, name


def test_imp8_phone_tap_targets_and_tab_bar_space(css):
    phone = re.search(r"@media \(max-width: 720px\) \{(.*?)\n\}", css, re.S)
    assert phone, "a phone-width section"
    rules = phone.group(1)
    assert "min-height: 44px" in rules
    assert re.search(r"body \{[^}]*padding-bottom:", rules)
    assert re.search(r"\.main-nav \{[^}]*position: fixed", rules)


def test_imp8_no_outside_assets(web):
    html = web.get("/").text
    # url_for gives the app's own address (http://testserver/static/...); nothing else.
    assert not re.search(r'(src|href)="(https?:)?//(?!testserver/)', html.split("<main")[0])
    css = (STATIC_DIR / "style.css").read_text()
    assert "@import" not in css
    assert not re.search(r"url\(\s*['\"]?(https?:)?//", css)
