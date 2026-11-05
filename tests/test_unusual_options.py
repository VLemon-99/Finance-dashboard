"""SPEC F4.8: unusual options activity = today's volume > 2x open interest and volume >= 500
(thresholds in config/institutional.yaml)."""

from datetime import UTC, date, datetime

import pytest

import app.services.market_facts as market_facts
from app.config import BenchmarksConfig, InstitutionalConfig, UnusualOptionsConfig
from app.providers.market_data import OptionChainData, OptionContractData
from app.services.market_facts import MarketFacts
from app.services.unusual_options import is_unusual, scan, unusual_contracts
from tests.conftest import FakeMarketData

CFG = UnusualOptionsConfig()
TODAY = date(2026, 11, 4)


def contract(option_type, strike, volume, open_interest):
    return OptionContractData(
        contract_symbol=f"X{option_type}{strike}",
        option_type=option_type,
        strike=strike,
        volume=volume,
        open_interest=open_interest,
        last_price=1.5,
        implied_volatility=0.42,
    )


def chain(*contracts, expiration=date(2026, 11, 20)):
    return OptionChainData(
        underlying="AAPL",
        expiration=expiration,
        underlying_price=260.0,
        contracts=contracts,
        fetched_at=datetime(2026, 11, 4, 18, 0, tzinfo=UTC),
        source="yahoo",
    )


@pytest.mark.parametrize(
    ("volume", "open_interest", "unusual"),
    [
        (1200, 100, True),  # 12x
        (600, 0, True),  # nothing open yet: all new
        (1001, 500, True),  # just over 2x
        (1000, 500, False),  # exactly 2x is not "more than"
        (499, 0, False),  # under 500 contracts
        (None, 0, False),
    ],
)
def test_thresholds(volume, open_interest, unusual):
    assert is_unusual(volume, open_interest, CFG) is unusual


def test_thresholds_come_from_the_settings():
    loose = UnusualOptionsConfig(volume_to_open_interest=1.5, min_volume=100)
    assert is_unusual(160, 100, loose) and not is_unusual(160, 100, CFG)


def test_unusual_contracts_sorted_by_volume_with_ratio():
    rows = unusual_contracts(
        [
            chain(contract("call", 300, 1200, 100), contract("put", 210, 1000, 500)),
            chain(contract("put", 200, 2500, 0), expiration=date(2026, 12, 18)),
        ],
        CFG,
    )
    assert [(r["type"], r["strike"], r["volume"]) for r in rows] == [
        ("put", 200, 2500),
        ("call", 300, 1200),
    ]
    assert rows[0]["ratio"] is None and rows[0]["expiration"] == "2026-12-18"
    assert rows[1]["ratio"] == pytest.approx(12.0)


def test_scan_only_the_next_60_days_and_at_most_6_dates():
    market = FakeMarketData()
    market.extra_contracts = [contract("call", 300, 1200, 100), contract("put", 200, 700, 50)]
    result = scan(market, "AAPL", TODAY, CFG)
    # Sample dates: Nov 6, 13, 20, Dec 18 (in the next 60 days), Jan 15 (72 days away).
    assert [d for _, d in market.chain_calls] == [
        date(2026, 11, 6),
        date(2026, 11, 13),
        date(2026, 11, 20),
        date(2026, 12, 18),
    ]
    assert result["expirations"] == 4
    assert (result["count"], result["calls"], result["puts"]) == (8, 4, 4)
    assert (result["call_volume"], result["put_volume"]) == (4800, 2800)
    assert len(result["contracts"]) == 8

    market = FakeMarketData()
    scan(market, "AAPL", TODAY, UnusualOptionsConfig(max_expirations=2, max_rows=1))
    assert len(market.chain_calls) == 2


def test_quiet_day_and_no_options():
    market = FakeMarketData()
    quiet = scan(market, "AAPL", TODAY, CFG)  # the sample's volumes are all below 2x OI
    assert quiet["count"] == 0 and quiet["contracts"] == [] and quiet["expirations"] == 4
    market.no_options.add("AAPL")
    none = scan(market, "AAPL", TODAY, CFG)
    assert none["expirations"] == 0 and none["as_of"] is None


def gather(market, config=None):
    facts = MarketFacts(market, BenchmarksConfig(), institutional=config or InstitutionalConfig())
    return facts.gather(["AAPL"])["AAPL"]


def test_in_the_run_and_failures_listed(monkeypatch):
    monkeypatch.setattr(market_facts, "today_eastern", lambda: TODAY)
    market = FakeMarketData()
    market.extra_contracts = [contract("call", 300, 1200, 100)]
    assert gather(market).unusual_options["count"] == 4

    market.down_for.add("AAPL")  # Yahoo fails for this ticker's options
    facts = gather(market)
    assert facts.unusual_options is None
    assert "Unusual options activity: Yahoo Finance did not respond properly" in " ".join(
        facts.missing
    )


def test_switched_off():
    market = FakeMarketData()
    off = InstitutionalConfig(unusual_options=UnusualOptionsConfig(enabled=False))
    assert gather(market, off).unusual_options is None
    assert market.expiration_calls == []
