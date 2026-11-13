"""IMP-11: forms that are easy to fill in on a phone (keyboards, autofill, no autocorrect).

The stacked phone layout itself is CSS (checked with screenshots); these tests check the
field attributes phones rely on, old ones included, so they can't quietly disappear.
"""

import re

import pytest

from app.web.assets import STATIC_DIR

TICKER_ATTRS = ('autocapitalize="characters"', 'autocorrect="off"', 'spellcheck="false"')


def field(html, field_id):
    match = re.search(rf'<input id="{field_id}"[^>]*>', html, re.S)
    assert match, f"no field {field_id}"
    return match.group(0)


@pytest.fixture
def web(client, options_clock):
    client.post("/watchlists", data={"name": "Tech"})
    client.get("/options/AAPL?expiration=2026-11-20")
    return client


@pytest.mark.parametrize(
    ("path", "field_id"),
    [
        ("/watchlists/1", "ticker"),  # add by ticker
        ("/watchlists/1", "q"),  # search by ticker or company name
        ("/lookup", "ticker"),
        ("/options", "ticker"),
        ("/portfolio", "symbol"),  # record a stock trade
    ],
)
def test_imp11_ticker_fields_are_not_autocorrected(web, path, field_id):
    tag = field(web.get(path).text, field_id)
    for attr in TICKER_ATTRS:
        assert attr in tag, f"{attr} missing on {path} #{field_id}"


def test_imp11_search_field_keeps_browser_suggestions_off(web):
    assert 'autocomplete="off"' in field(web.get("/watchlists/1").text, "q")


def test_imp11_trade_form_opens_number_keyboards(web):
    html = web.get("/portfolio/trade?symbol=AAPL261120P00250000&side=sold").text
    assert 'inputmode="numeric"' in field(html, "quantity")
    assert 'inputmode="decimal"' in field(html, "price")
    assert 'inputmode="decimal"' in field(html, "underlying_price")
    # A stock trade has a price field too
    html = web.get("/portfolio/trade?symbol=AAPL&side=bought").text
    assert 'inputmode="decimal"' in field(html, "price")


def test_imp11_login_fields_autofill(web):
    html = web.get("/login").text
    email = field(html, "email")
    assert 'type="email"' in email and 'autocomplete="username"' in email
    assert 'autocapitalize="none"' in email
    assert 'autocomplete="current-password"' in field(html, "password")


def test_imp11_signup_fields_autofill(web):
    html = web.get("/signup").text
    email = field(html, "email")
    assert 'type="email"' in email and 'autocomplete="username"' in email
    assert 'autocomplete="new-password"' in field(html, "password")
    assert 'autocomplete="new-password"' in field(html, "password_again")


def test_imp11_list_name_fields_capitalise_like_sentences(web):
    for path, field_id in (("/", "name"), ("/watchlists/1", "rename")):
        assert 'autocapitalize="sentences"' in field(web.get(path).text, field_id)


def test_imp11_phone_css_stacks_fields():
    css = (STATIC_DIR / "style.css").read_text()
    phone = re.findall(r"@media \(max-width: 720px\) \{(.*?)\n\}", css, re.S)
    rules = "\n".join(phone)
    assert re.search(r"\.inline-form[^{]*\{[^}]*grid-template-columns: 1fr", rules)
    assert re.search(r"\.trade-form \.field[^{]*\{[^}]*flex-direction: column", rules)


def test_imp11_full_width_rule_leaves_checkboxes_alone():
    """The calendar filters put checkboxes inside .inline-form labels: a descendant
    `.inline-form input { width: 100% }` stretched them across the screen."""
    css = (STATIC_DIR / "style.css").read_text()
    for rule in re.findall(r"([^{}]*)\{[^}]*width: 100%", css):
        for selector in rule.split(","):
            assert not re.search(r"\.(inline|quote)-form\s+input", selector.strip()), selector
