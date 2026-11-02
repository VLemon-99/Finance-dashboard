"""SEC EDGAR (SPEC F4.3, F4.8): filings list, 8-K text, Form 4 trades, from recorded samples."""

from datetime import UTC, datetime, timedelta

import pytest

from app.config import EdgarConfig
from app.sources.base import SourceError
from app.sources.edgar import (
    EdgarSource,
    form4_text,
    form_title,
    parse_acceptance_time,
    parse_form4,
    short_company_name,
)
from tests.conftest import SOURCE_FIXTURES

UA = "Test Person test.person@mail.test"
BASE = "https://www.sec.gov/Archives/edgar/data/320193"


@pytest.fixture
def edgar(fake_fetch):
    return EdgarSource(fake_fetch, EdgarConfig(), UA)


@pytest.fixture
def since(sources_now):
    return sources_now - timedelta(days=7)


def test_filings_in_the_window(edgar, since):
    items = edgar.fetch("AAPL", "Apple", since)
    # Report window: 7 days. Form 4s go back 90 days (Sep 15 kept, Jul 20 dropped).
    # The S-8 isn't on the forms list; the May 10-Q is too old.
    assert [i.form for i in items] == ["4", "SCHEDULE 13G", "8-K", "10-K", "4"]
    assert all(i.source == "sec_edgar" and i.outlet == "SEC EDGAR" for i in items)
    assert all(i.source_type == "filing" for i in items)


def test_eight_k_item(edgar, since):
    eight_k = next(i for i in edgar.fetch("AAPL", "Apple", since) if i.form == "8-K")
    assert eight_k.url == f"{BASE}/000032019326000100/aapl-20261030.htm"
    assert eight_k.title == (
        "Apple Inc.: Form 8-K: Current report — Results of operations and financial condition"
    )
    # 4:30:45 PM Eastern daylight time = 8:30:45 PM UTC.
    assert eight_k.published_at == datetime(2026, 10, 30, 20, 30, 45, tzinfo=UTC)


def test_acceptance_time_is_eastern_clock_time():
    # Nov 2 is after daylight saving ends (Nov 1, 2026): Eastern = UTC-5.
    assert parse_acceptance_time("2026-11-02T09:00:00.000Z") == datetime(
        2026, 11, 2, 14, 0, tzinfo=UTC
    )


def test_form4_item_links_to_readable_page_and_raw_data(edgar, since):
    form4 = edgar.fetch("AAPL", "Apple", since)[0]
    assert form4.url == f"{BASE}/000032019326000101/xslF345X05/wk-form4_1730412312.xml"
    assert form4.data["xml_url"] == f"{BASE}/000032019326000101/wk-form4_1730412312.xml"


def test_enrich_form4_reads_the_trades(edgar, since):
    form4 = edgar.enrich(edgar.fetch("AAPL", "Apple", since)[0])
    assert form4.data["owners"] == [{"name": "Example Jane Q", "role": "Chief Financial Officer"}]
    assert form4.data["trades"] == [
        {
            "date": "2026-10-29",
            "kind": "sell",
            "shares": 12000.0,
            "price": 250.25,
            "shares_after": 108000.0,
        },
        {
            "date": "2026-10-30",
            "kind": "sell",
            "shares": 3000.0,
            "price": 251.5,
            "shares_after": 105000.0,
        },
    ]
    assert form4.data["other_transactions"] == 1  # the option exercise (code M)
    assert form4.data["plan_10b5_1"] is True
    assert form4.text == (
        "Example Jane Q (Chief Financial Officer) sold 12,000 shares at $250.25 on 2026-10-29; "
        "sold 3,000 shares at $251.50 on 2026-10-30 (pre-arranged 10b5-1 trading plan)."
    )


def test_form4_buy_by_a_director():
    parsed = parse_form4((SOURCE_FIXTURES / "sec_form4_buy.xml").read_bytes())
    assert parsed["owners"] == [{"name": "Sample John R", "role": "Director"}]
    assert [(t["kind"], t["shares"], t["price"]) for t in parsed["trades"]] == [
        ("buy", 1000.0, 230.0)
    ]
    assert parsed["plan_10b5_1"] is False


def test_form4_without_buys_or_sells():
    text = form4_text(
        {"owners": [{"name": "A B", "role": "Director"}], "trades": [], "plan_10b5_1": False}
    )
    assert (
        text
        == "A B (Director): no open-market buys or sells (grants, exercises or other changes only)."
    )


def test_broken_form4_is_a_source_error():
    with pytest.raises(SourceError):
        parse_form4(b"<not-closed")
    with pytest.raises(SourceError):
        parse_form4(b"<html></html>")


def test_enrich_eight_k_adds_the_press_release_text(edgar, since, fake_fetch):
    eight_k = next(i for i in edgar.fetch("AAPL", "Apple", since) if i.form == "8-K")
    text = edgar.enrich(eight_k).text
    assert "Item 2.02 Results of Operations and Financial Condition." in text
    assert "Example revenue of $100.0 billion" in text  # from exhibit 99.1
    assert "should not appear" not in text  # script content is dropped
    assert not any(c.endswith("R1.htm") for c in fake_fetch.calls)


def test_filing_text_is_cut_to_the_configured_length(fake_fetch, since):
    edgar = EdgarSource(fake_fetch, EdgarConfig(max_text_chars=1000), UA)
    eight_k = next(i for i in edgar.fetch("AAPL", "Apple", since) if i.form == "8-K")
    assert len(edgar.enrich(eight_k).text) <= 1000


def test_missing_exhibit_list_still_gives_main_text(edgar, since, fake_fetch):
    fake_fetch.down.add(f"{BASE}/000032019326000100/index.json")
    eight_k = next(i for i in edgar.fetch("AAPL", "Apple", since) if i.form == "8-K")
    assert "Item 2.02" in edgar.enrich(eight_k).text


def test_ticker_not_registered_with_the_sec_gives_nothing(edgar, since):
    assert edgar.fetch("SPY", None, since) == []


def test_company_name_lookup(edgar):
    assert edgar.company_name("AAPL") == "Apple"
    assert edgar.company_name("BRK-B") == "BERKSHIRE HATHAWAY"
    assert edgar.company_name("ZZZZ") is None


def test_ticker_list_is_downloaded_once(edgar, fake_fetch, since):
    edgar.fetch("AAPL", None, since)
    edgar.company_name("MSFT")
    assert sum(c.endswith("company_tickers.json") for c in fake_fetch.calls) == 1


@pytest.mark.parametrize("ua", ["", "Your Name your-email@example.com", "nocontact"])
def test_missing_contact_details_is_a_clear_error(fake_fetch, since, ua):
    edgar = EdgarSource(fake_fetch, EdgarConfig(), ua)
    with pytest.raises(SourceError, match="SEC_USER_AGENT"):
        edgar.fetch("AAPL", None, since)
    assert fake_fetch.calls == []  # nothing is sent to the SEC without a contact


@pytest.mark.parametrize(
    ("sec_name", "short"),
    [
        ("Apple Inc.", "Apple"),
        ("MICROSOFT CORP", "MICROSOFT"),
        ("NVIDIA CORP /DE/", "NVIDIA"),
        ("Meta Platforms, Inc.", "Meta Platforms"),
        ("UnitedHealth Group Inc", "UnitedHealth"),
        ("Ford Motor Co", "Ford Motor"),
        ("Coca-Cola Co", "Coca-Cola"),
    ],
)
def test_short_company_name(sec_name, short):
    assert short_company_name(sec_name) == short


def test_form_titles():
    assert form_title("10-Q", "") == "Form 10-Q: Quarterly report"
    assert form_title("8-K/A", "5.02") == (
        "Form 8-K (amended): Current report — Director or officer change, or executive pay"
    )
    assert form_title("SC 13G", "") == "Form SC 13G: Ownership report by a passive 5%+ holder"
