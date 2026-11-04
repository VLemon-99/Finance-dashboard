"""SPEC F4.8: holdings of the funds the owner follows, from their 13F reports (hand-made
samples in the SEC's formats)."""

from datetime import date

import pytest

from app.config import FollowedFund, FollowedFundsConfig
from app.providers.sec_13f import (
    ThirteenFClient,
    information_table_name,
    issuer_key,
    parse_fund_filings,
    parse_information_table,
)
from app.services.market_facts import fund_positions
from app.sources.base import SourceError
from tests.conftest import (
    FUND_CIK,
    FUND_ROUTES,
    SOURCE_FIXTURES,
    TEST_SEC_USER_AGENT,
    FakeFetch,
)

BERKSHIRE = FollowedFund(name="Berkshire Hathaway", cik=FUND_CIK)


@pytest.fixture
def fetch():
    return FakeFetch(FUND_ROUTES)


def reports(fetch):
    return ThirteenFClient(fetch, TEST_SEC_USER_AGENT).latest_reports(FUND_CIK)


def test_latest_two_original_reports_amendments_skipped():
    filings = parse_fund_filings((SOURCE_FIXTURES / "sec13f_submissions.json").read_bytes())
    assert [(f["period"], f["accession"]) for f in filings[:2]] == [
        (date(2026, 6, 30), "0000950123-26-008001"),
        (date(2026, 3, 31), "0000950123-26-005001"),
    ]
    assert filings[0]["fund"] == "BERKSHIRE HATHAWAY INC"


def test_holdings_file_is_the_xml_that_isnt_the_cover_page():
    assert information_table_name((SOURCE_FIXTURES / "sec13f_index_latest.json").read_bytes()) == (
        "46994.xml"
    )
    with pytest.raises(SourceError):
        information_table_name(b'{"directory": {"item": [{"name": "primary_doc.xml"}]}}')


def test_information_table_adds_up_rows_and_skips_options_and_bonds():
    positions = {
        p.cusip: p
        for p in parse_information_table(
            (SOURCE_FIXTURES / "sec13f_infotable_latest.xml").read_bytes()
        )
    }
    amazon = positions["023135106"]
    assert (amazon.shares, amazon.value) == (10_000_000, 2_500_000_000)  # two rows, put left out
    assert "674599CH6" not in positions  # a bond
    assert sum(p.value for p in positions.values()) == 100_000_000_000


def test_client_reads_both_quarters_and_keeps_reports(fetch):
    latest, previous = reports(fetch)
    assert (latest.period, latest.filed, previous.period) == (
        date(2026, 6, 30),
        date(2026, 8, 14),
        date(2026, 3, 31),
    )
    assert latest.total_value == 100_000_000_000
    client = ThirteenFClient(fetch, TEST_SEC_USER_AGENT)
    client.latest_reports(FUND_CIK)
    calls = len(fetch.calls)
    client.latest_reports(FUND_CIK)
    assert len(fetch.calls) == calls  # list reused for 12 hours, reports kept for good


def test_needs_the_sec_contact(fetch):
    with pytest.raises(SourceError, match="SEC_USER_AGENT"):
        ThirteenFClient(fetch, "").latest_reports(FUND_CIK)
    assert fetch.calls == []


def test_unknown_cik(fetch):
    with pytest.raises(SourceError):
        ThirteenFClient(fetch, TEST_SEC_USER_AGENT).latest_reports(123)


@pytest.mark.parametrize(
    ("thirteen_f", "everyday"),
    [
        ("AMAZON COM INC", "Amazon.com, Inc."),
        ("OCCIDENTAL PETE CORP", "Occidental Petroleum Corporation"),
        ("BANK AMERICA CORP", "Bank of America Corporation"),
        ("JPMORGAN CHASE & CO", "JPMorgan Chase & Co."),
        ("BERKSHIRE HATHAWAY INC DEL", "Berkshire Hathaway Inc."),
        ("HOME DEPOT INC", "The Home Depot, Inc."),
    ],
)
def test_13f_names_match_everyday_names(thirteen_f, everyday):
    assert issuer_key(thirteen_f) == issuer_key(everyday)


def test_different_companies_dont_match():
    assert issuer_key("GENERAL MTRS CO") != issuer_key("General Mills, Inc.")
    assert issuer_key("APPLE INC") != issuer_key("Apple Hospitality REIT, Inc.")


def positions_for(fetch, *names, cusip=None):
    return fund_positions([(BERKSHIRE, *reports(fetch))], names, cusip)


def test_reduced_stake_known_answer(fetch):
    [amzn] = positions_for(fetch, "Amazon.com, Inc.")
    assert amzn["status"] == "reduced"
    assert amzn["change"] == pytest.approx(-0.2)  # 12.5M -> 10M shares
    assert amzn["portfolio_pct"] == pytest.approx(0.025)  # $2.5B of $100B
    assert (amzn["shares"], amzn["period"]) == (10_000_000, "2026-06-30")


def test_unchanged_added_after_a_rename_sold_and_not_held(fetch):
    assert positions_for(fetch, "Apple Inc.")[0]["status"] == "unchanged"
    # Reported as "FACEBOOK INC" last quarter: the shared CUSIP links the two quarters.
    meta = positions_for(fetch, "Meta Platforms, Inc.")[0]
    assert (meta["status"], meta["change"]) == ("added", pytest.approx(0.2))
    visa = positions_for(fetch, "Visa Inc.")[0]
    assert (visa["status"], visa["shares"], visa["change"]) == ("sold", 0.0, -1.0)
    assert positions_for(fetch, "Microsoft Corporation") == []


def test_all_share_classes_count_together(fetch):
    [alphabet] = positions_for(fetch, "Alphabet Inc.")
    assert alphabet["shares"] == 9_000_000 and alphabet["status"] == "new"


def test_owner_given_cusip(fetch):
    [oxy] = positions_for(fetch, "Some Name Nobody Uses", cusip="674599105")
    assert oxy["shares"] == 260_000_000


def test_cusip_setting_is_validated():
    assert FollowedFundsConfig(cusips={"oxy": "674599105"}).cusips == {"OXY": "674599105"}
    with pytest.raises(ValueError, match="CUSIP"):
        FollowedFundsConfig(cusips={"OXY": "12345"})
