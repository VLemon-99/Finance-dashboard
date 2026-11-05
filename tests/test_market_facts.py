"""SPEC F4.7: automatic comparison groups (own history, industry peers, sector, Damodaran),
the owner's overrides, sharing figures between tickers, and failures (F4.11)."""

import pytest

from app.config import (
    PROJECT_ROOT,
    BenchmarksConfig,
    DamodaranConfig,
    FollowedFund,
    TickerBenchmarks,
    load_benchmarks_config,
)
from app.providers.damodaran import DamodaranClient
from app.services.market_facts import MarketFacts
from tests.conftest import (
    FUND_ROUTES,
    TEST_SEC_USER_AGENT,
    FakeFetch,
    FakeMarketData,
)
from tests.test_damodaran import PageFetch

BERKSHIRE = FollowedFund(name="Berkshire Hathaway", cik=1067983)
OWNER_CONFIG = load_benchmarks_config(PROJECT_ROOT / "config" / "benchmarks.yaml")


@pytest.fixture
def market():
    return FakeMarketData()


@pytest.fixture
def pages():
    return PageFetch()


def facts_for(market, pages, config=OWNER_CONFIG, damodaran=True):
    client = DamodaranClient(pages, DamodaranConfig()) if damodaran else None
    return MarketFacts(market, config, client, max_parallel=3)


def row(valuation, key="trailing_pe"):
    return next(r for r in valuation["rows"] if r["key"] == key)


def test_full_valuation_known_answers(market, pages):
    facts = facts_for(market, pages).gather(["AAPL"])["AAPL"]
    assert facts.missing == []
    v = facts.valuation
    assert (v["sector"], v["industry"], v["price"]) == ("Technology", "Consumer Electronics", 200)

    pe = row(v)
    assert pe["now"] == 30.0
    # Own history: P/E 20, 25, 30 (FY2025 was a loss): average 25, so 20% above it.
    assert pe["history"]["average"] == pytest.approx(25.0)
    assert (pe["history"]["low"], pe["history"]["high"]) == (20.0, 30.0)
    assert pe["history"]["vs"] == pytest.approx(0.2)
    # Industry peers SONY 20, GPRO (loss), LPL 10: median 15 from 2 companies; 100% above.
    assert pe["industry"] == {"value": 15.0, "count": 2, "vs": pytest.approx(1.0)}
    # Sector: Technology's largest without AAPL: NVDA 50, MSFT 35, ORCL 40 -> 40.
    assert pe["sector"]["value"] == 40.0 and pe["sector"]["vs"] == pytest.approx(-0.25)
    # Damodaran "Electronics (Consumer & Office)" trailing P/E 26.
    assert pe["damodaran"]["value"] == 26.0

    assert row(v, "forward_pe")["history"] is None  # no history for expected earnings
    assert row(v, "forward_pe")["industry"]["value"] == 18.0
    assert row(v, "ev_ebitda")["industry"]["value"] == 8.0
    assert row(v, "price_to_sales")["sector"]["value"] == 12.0

    assert v["industry_peers"]["name"] == "Consumer Electronics"
    assert [p["symbol"] for p in v["industry_peers"]["peers"]] == ["SONY", "GPRO", "LPL"]
    assert v["sector_group"] == {"name": "Technology", "override": False, "count": 3}
    assert v["damodaran"] == {
        "industry": "Electronics (Consumer & Office)",
        "updated": "January 2026",
    }
    assert v["history"]["span"] == "FY2022–FY2025"
    assert [y["year"] for y in v["history"]["years"]] == ["FY2022", "FY2023", "FY2024", "FY2025"]
    t = v["targets"]
    assert (t["analysts"], t["low"], t["mean"], t["high"]) == (40, 180.0, 230.0, 260.0)
    assert t["mean_vs"] == pytest.approx(0.15)


def test_amzn_is_compared_with_technology_by_the_owners_setting(market, pages):
    v = facts_for(market, pages).gather(["AMZN"])["AMZN"].valuation
    assert v["sector"] == "Consumer Cyclical"  # Yahoo's own classification, still shown
    assert v["sector_group"] == {"name": "Technology", "override": True, "count": 4}
    # NVDA 50, MSFT 35, AAPL 30, ORCL 40 -> median 37.5
    assert row(v)["sector"]["value"] == 37.5
    # Industry peers stay automatic: Internet Retail's BABA 15 and MELI 55 -> 35.
    assert v["industry_peers"]["name"] == "Internet Retail"
    assert row(v)["industry"]["value"] == 35.0
    assert v["damodaran"]["industry"] == "Retail (Online)"
    assert "group:sector:consumer-cyclical" not in market.research_calls


def test_own_peer_list_replaces_the_industry(market, pages):
    config = BenchmarksConfig(tickers={"AAPL": TickerBenchmarks(peers=["msft", "NVDA", "AAPL"])})
    v = facts_for(market, pages, config).gather(["AAPL"])["AAPL"].valuation
    assert v["industry_peers"]["name"] == "Your peers" and v["industry_peers"]["custom"]
    assert row(v)["industry"]["value"] == 42.5  # MSFT 35, NVDA 50
    assert "group:industry:consumer-electronics" not in market.research_calls


def test_overrides_are_validated():
    with pytest.raises(ValueError, match="Yahoo's sectors"):
        TickerBenchmarks(sector="Tech stuff")
    with pytest.raises(ValueError, match="valid ticker"):
        TickerBenchmarks(peers=["not a ticker"])
    assert TickerBenchmarks(sector="Consumer Cyclical").sector == "consumer-cyclical"
    assert TickerBenchmarks(industry="Software - Infrastructure").industry == (
        "software-infrastructure"
    )
    assert BenchmarksConfig(tickers={"brk.b": {}}).tickers.keys() == {"BRK-B"}


def test_groups_and_peers_are_fetched_once_and_reused(market, pages):
    facts = facts_for(market, pages)
    facts.gather(["AAPL", "SONY"])  # SONY is both a ticker and AAPL's peer
    assert market.research_calls.count("profile:SONY") == 1
    assert market.research_calls.count("group:sector:technology") == 1
    calls = len(market.research_calls)
    facts.gather(["AAPL"])  # a later run the same day
    new = market.research_calls[calls:]
    # Its own yearly figures and holders are asked again; groups and peers are reused.
    assert sorted(new) == ["figures:AAPL", "ownership:AAPL"]
    assert len(pages.calls) == 3


def test_missing_parts_are_listed_and_the_rest_still_shown(market, pages):
    market.research_down |= {"figures:AAPL", "group:sector:technology"}
    pages.down.add("pedata.html")
    facts = facts_for(market, pages).gather(["AAPL"])["AAPL"]
    v = facts.valuation
    assert v["history"] is None and v["sector_group"] is None
    assert row(v)["industry"]["value"] == 15.0
    assert row(v)["damodaran"] is None and row(v, "ev_ebitda")["damodaran"]["value"] == 13.2
    joined = " | ".join(facts.missing)
    assert "Valuation history: Yahoo Finance did not respond properly" in joined
    assert "Valuation, sector benchmark: Yahoo Finance did not respond properly" in joined
    assert "Damodaran industry averages, pe page" in joined


def test_ticker_without_figures(market, pages):
    market.research_down.add("profile:AAPL")
    result = facts_for(market, pages).gather(["AAPL", "ZZZZ"])
    assert result["AAPL"].valuation is None
    assert result["AAPL"].missing == [
        "Valuation: Yahoo Finance did not respond properly (Timeout)."
    ]
    assert result["ZZZZ"].missing == ["Valuation: Yahoo Finance has no data for this ticker."]
    assert pages.calls == []  # nothing to compare, so Damodaran isn't downloaded


def test_damodaran_industry_not_set_or_not_found(market, pages):
    config = BenchmarksConfig(
        tickers={"AMZN": TickerBenchmarks(damodaran_industry="Retail (Spaceships)")}
    )
    result = facts_for(market, pages, config).gather(["AAPL", "AMZN"])
    assert "no Damodaran industry is set for 'Consumer Electronics'" in result["AAPL"].missing[0]
    assert "no industry called 'Retail (Spaceships)'" in result["AMZN"].missing[0]
    assert result["AAPL"].valuation["damodaran"] is None


def test_damodaran_switched_off(market, pages):
    config = BenchmarksConfig(damodaran=DamodaranConfig(enabled=False))
    facts = facts_for(market, pages, config).gather(["AAPL"])["AAPL"]
    assert facts.missing == [] and facts.valuation["damodaran"] is None
    assert pages.calls == []


def test_owner_config_maps_every_yahoo_industry_in_the_sample(market):
    from app.services.market_facts import damodaran_name

    assert damodaran_name(OWNER_CONFIG, "internet-retail") == "Retail (Online)"
    assert damodaran_name(OWNER_CONFIG, "oil-gas-ep") == "Oil/Gas (Production and Exploration)"
    assert damodaran_name(OWNER_CONFIG, "banks-regional") == "Banks (Regional)"
    assert damodaran_name(OWNER_CONFIG, "shell-companies") is None


# --- institutional holders and followed funds (SPEC F4.8) ----------------------------


def with_funds(market, funds, user_agent=TEST_SEC_USER_AGENT):
    from app.config import FollowedFundsConfig, InstitutionalConfig
    from app.providers.sec_13f import ThirteenFClient

    config = InstitutionalConfig(followed_funds=FollowedFundsConfig(funds=funds))
    client = ThirteenFClient(FakeFetch(FUND_ROUTES), user_agent)
    return MarketFacts(market, OWNER_CONFIG, None, client, config, max_parallel=2)


def test_top_institutional_holders(market, pages):
    inst = facts_for(market, pages).gather(["AAPL"])["AAPL"].institutional
    own = inst["ownership"]
    assert (own["institutions_pct"], own["institutions_count"]) == (0.6231, 6512)
    assert own["as_of"] == "2025-06-30"
    assert [h["name"] for h in own["top"]][:2] == ["Vanguard Group Inc", "Blackrock Inc."]
    assert own["top"][1]["pct_change"] == -0.0081
    assert inst["funds"] is None  # no funds followed in this setup


def test_followed_funds_in_the_report(market):
    result = with_funds(market, [BERKSHIRE]).gather(["AMZN", "AAPL"])
    funds = result["AMZN"].institutional["funds"]
    assert (funds["checked"], funds["period"]) == (1, "2026-06-30")
    [amzn] = funds["positions"]
    assert (amzn["fund"], amzn["status"]) == ("Berkshire Hathaway", "reduced")
    assert result["AAPL"].institutional["funds"]["positions"][0]["status"] == "unchanged"
    assert result["AMZN"].missing == []


def test_sec_company_name_helps_find_the_stock(market):
    result = with_funds(market, [BERKSHIRE]).gather(
        ["OXY"], company_names=lambda t: "OCCIDENTAL PETROLEUM CORP /DE/"
    )
    [oxy] = result["OXY"].institutional["funds"]["positions"]
    assert oxy["shares"] == 260_000_000


def test_one_fund_failing_is_listed_and_others_still_shown(market):
    broken = FollowedFund(name="Wrong Fund", cik=123)
    facts = with_funds(market, [broken, BERKSHIRE]).gather(["AMZN"])["AMZN"]
    assert facts.institutional["funds"]["checked"] == 1
    assert any(m.startswith("Funds you follow (Wrong Fund): ") for m in facts.missing)


def test_funds_without_the_sec_contact_give_one_note(market):
    two = [BERKSHIRE, FollowedFund(name="Other", cik=1336528)]
    facts = with_funds(market, two, user_agent="").gather(["AMZN"])["AMZN"]
    assert facts.institutional["funds"] is None
    assert [m for m in facts.missing if "Funds" in m] == [
        "Funds you follow: SEC filings need your contact details: set SEC_USER_AGENT in the "
        ".env file."
    ]


def test_holders_down(market, pages):
    market.research_down.add("ownership:AAPL")
    facts = facts_for(market, pages).gather(["AAPL"])["AAPL"]
    assert facts.institutional["ownership"] is None
    assert facts.missing == [
        "Top institutional holders: Yahoo Finance did not respond properly (Timeout)."
    ]
