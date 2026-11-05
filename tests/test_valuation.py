"""SPEC F4.7: valuation numbers are worked out in code, each formula checked with known answers."""

from datetime import date

import pytest

from app.providers.market_data import AnnualFigures, CompanyProfile, Multiples, TickerNotFound
from app.providers.yahoo import (
    annual_figures_from_yahoo,
    close_on_or_before,
    group_from_yahoo,
    ownership_from_yahoo,
    profile_from_info,
)
from app.services.valuation import (
    group_benchmark,
    group_median,
    history_multiples,
    metric_rows,
    own_range,
    relative,
    target_section,
    year_multiples,
)
from tests.conftest import load_yahoo_research

BILLION = 1_000_000_000


def figures(**kw) -> AnnualFigures:
    base = dict(
        period_end=date(2024, 12, 31),
        price=100.0,
        diluted_eps=4.0,
        revenue=500.0,
        ebitda=100.0,
        total_debt=200.0,
        cash=50.0,
        shares=10.0,
    )
    return AnnualFigures(**{**base, **kw})


# --- one year's multiples -------------------------------------------------------------


def test_year_multiples_known_answers():
    # P/E = 100 / 4 = 25. Market cap = 100 x 10 = 1,000. P/S = 1,000 / 500 = 2.
    # EV = 1,000 + 200 - 50 = 1,150. EV/EBITDA = 1,150 / 100 = 11.5.
    m = year_multiples(figures())
    assert m.pe == pytest.approx(25.0)
    assert m.ps == pytest.approx(2.0)
    assert m.ev_ebitda == pytest.approx(11.5)


def test_losses_make_multiples_not_meaningful():
    assert year_multiples(figures(diluted_eps=-1.0)).pe is None
    assert year_multiples(figures(diluted_eps=0.0)).pe is None
    assert year_multiples(figures(ebitda=-20.0)).ev_ebitda is None
    # More cash than market cap + debt: negative enterprise value, not meaningful.
    assert year_multiples(figures(cash=5_000.0)).ev_ebitda is None


def test_missing_pieces_leave_only_that_multiple_out():
    m = year_multiples(figures(shares=None))
    assert m.pe == pytest.approx(25.0)
    assert m.ps is None and m.ev_ebitda is None
    m = year_multiples(figures(total_debt=None, cash=None))
    assert m.ev_ebitda == pytest.approx(10.0)  # EV = market cap = 1,000
    assert year_multiples(figures(price=None)) == year_multiples(figures(price=None, shares=1))


def test_history_skips_years_that_straddle_a_stock_split():
    years, skipped = history_multiples(
        [
            figures(period_end=date(2021, 12, 31), shares=200.0, diluted_eps=80.0),  # pre-split
            figures(period_end=date(2023, 12, 31)),
            figures(period_end=date(2022, 12, 31), shares=12.0),
        ],
        current_shares=10.0,
    )
    assert skipped == 1
    assert [y.period_end.year for y in years] == [2022, 2023]  # oldest first


# --- ranges, medians, comparisons -----------------------------------------------------


def test_own_range_known_answer():
    span = own_range([20.0, 30.0, None, -5.0, 40.0])  # the loss year and the gap don't count
    assert (span.average, span.low, span.high, span.years) == (30.0, 20.0, 40.0, 3)
    assert own_range([None, -1.0]) is None


def test_group_median_known_answers():
    assert group_median([10.0, 30.0, 20.0, None, -3.0]) == (20.0, 3)
    assert group_median([10.0, 20.0]) == (15.0, 2)
    assert group_median([]) == (None, 0)


def test_relative_known_answers():
    assert relative(30.0, 20.0) == pytest.approx(0.5)  # 50% above
    assert relative(15.0, 20.0) == pytest.approx(-0.25)  # 25% below
    assert relative(None, 20.0) is None
    assert relative(30.0, None) is None
    assert relative(-1.0, 20.0) is None


def profile(symbol: str, **multiples) -> CompanyProfile:
    return CompanyProfile(
        symbol=symbol,
        name=symbol,
        sector=None,
        sector_key=None,
        industry=None,
        industry_key=None,
        price=200.0,
        market_cap=None,
        shares_outstanding=None,
        multiples=Multiples(**multiples),
    )


def test_group_benchmark_takes_each_multiples_median():
    bench = group_benchmark(
        [
            profile("A", trailing_pe=20.0, forward_pe=18.0, price_to_sales=1.5),
            profile("B", forward_pe=40.0, price_to_sales=0.6),  # a loss: no P/E
            profile("C", trailing_pe=10.0, forward_pe=9.0, price_to_sales=0.3),
        ]
    )
    assert bench["trailing_pe"] == {"value": 15.0, "count": 2}
    assert bench["forward_pe"] == {"value": 18.0, "count": 3}
    assert bench["ev_ebitda"] == {"value": None, "count": 0}
    assert bench["price_to_sales"] == {"value": 0.6, "count": 3}


def test_target_gaps_known_answers():
    p = CompanyProfile(
        **{
            **profile("X").__dict__,
            "target_low": 180.0,
            "target_mean": 250.0,
            "target_high": 300.0,
            "analyst_count": 12,
        }
    )
    t = target_section(p)  # price 200
    assert t["analysts"] == 12
    assert t["low_vs"] == pytest.approx(-0.10)
    assert t["mean_vs"] == pytest.approx(0.25)
    assert t["high_vs"] == pytest.approx(0.50)
    assert target_section(profile("Y")) is None


def test_metric_rows_combine_history_and_benchmarks():
    years, _ = history_multiples(
        [figures(period_end=date(2023, 12, 31)), figures(period_end=date(2024, 12, 31), price=150)],
        current_shares=10.0,
    )  # P/E 25 and 37.5: average 31.25
    rows = metric_rows(
        Multiples(trailing_pe=25.0, forward_pe=20.0),
        years,
        {"industry": {"trailing_pe": {"value": 20.0, "count": 4}}, "sector": None},
    )
    pe = rows[0]
    assert pe["label"] == "P/E" and pe["now"] == 25.0
    assert pe["history"]["average"] == pytest.approx(31.25)
    assert pe["history"]["vs"] == pytest.approx(25 / 31.25 - 1)
    assert pe["industry"] == {"value": 20.0, "count": 4, "vs": pytest.approx(0.25)}
    assert pe["sector"] is None
    forward = rows[1]
    assert forward["history"] is None  # no history for expected earnings
    assert forward["industry"] is None


# --- reading Yahoo's answers (hand-made sample in yfinance's format) -------------------


def test_profile_from_yahoo_info():
    sample = load_yahoo_research()
    p = profile_from_info("AAPL", sample["info"]["AAPL"])
    assert (p.sector, p.sector_key, p.industry_key) == (
        "Technology",
        "technology",
        "consumer-electronics",
    )
    assert p.price == 200.0 and p.shares_outstanding == 15 * BILLION
    assert p.multiples == Multiples(
        trailing_pe=30.0, forward_pe=27.0, ev_ebitda=22.0, price_to_sales=7.5
    )
    assert (p.target_low, p.target_mean, p.target_high, p.analyst_count) == (
        180.0,
        230.0,
        260.0,
        40,
    )


def test_profile_drops_infinite_and_negative_multiples():
    p = profile_from_info("GPRO", load_yahoo_research()["info"]["GPRO"])
    assert p.multiples.trailing_pe is None  # Yahoo sends "Infinity" for a loss
    assert p.multiples.ev_ebitda is None  # negative
    assert p.multiples.forward_pe == 40.0


def test_profile_of_unknown_ticker():
    with pytest.raises(TickerNotFound):
        profile_from_info("ZZZZ", {"trailingPegRatio": None})


def test_annual_figures_from_yahoo_known_answers():
    s = load_yahoo_research()
    years = annual_figures_from_yahoo(s["income"], s["balance"], s["history"])
    assert [f.period_end for f in years] == [
        date(2022, 9, 30),
        date(2023, 9, 30),
        date(2024, 9, 30),
        date(2025, 9, 30),
    ]
    # 2023-09-30 was a Saturday: Friday's close is used. 2025-09-30 has no bar yet in the
    # sample, so the last close before it (Sep 26) is used.
    assert [f.price for f in years] == [100.0, 150.0, 120.0, 170.0]
    first = years[0]
    assert (first.diluted_eps, first.revenue, first.ebitda) == (5.0, 300 * BILLION, 100 * BILLION)
    assert (first.total_debt, first.cash) == (100 * BILLION, 50 * BILLION)
    assert first.shares == 15 * BILLION

    multiples, skipped = history_multiples(years, current_shares=15 * BILLION)
    assert skipped == 0
    # FY2022: P/E 100/5 = 20; P/S 1.5T/300B = 5; EV/EBITDA (1.5T+100B-50B)/100B = 15.5
    assert (multiples[0].pe, multiples[0].ps, multiples[0].ev_ebitda) == pytest.approx(
        (20.0, 5.0, 15.5)
    )
    # FY2025: a loss year, so no P/E; P/S 2.55T/420B
    assert multiples[3].pe is None
    assert multiples[3].ps == pytest.approx(2.55e12 / 420e9)
    span = own_range(m.pe for m in multiples)
    assert (span.average, span.low, span.high, span.years) == pytest.approx((25.0, 20, 30, 3))


def test_close_on_or_before_without_history():
    assert close_on_or_before(None, date(2024, 1, 1)) is None


def test_group_members_from_yahoo_skip_foreign_listings():
    name, top = load_yahoo_research()["groups"]["industry:consumer-electronics"]
    group = group_from_yahoo("industry", "consumer-electronics", name, top)
    assert group.name == "Consumer Electronics"
    assert group.symbols == ("AAPL", "SONY", "GPRO", "LPL")  # 6758.T (Tokyo) left out


def test_ownership_from_yahoo():
    s = load_yahoo_research()
    own = ownership_from_yahoo("AAPL", s["institutions"], s["breakdown"])
    assert own.institutions_pct == pytest.approx(0.6231)
    assert own.institutions_count == 6512
    top = own.top_institutions[0]
    assert top.name == "Vanguard Group Inc"
    assert (top.pct_held, top.pct_change, top.reported) == (0.0912, 0.0123, date(2025, 6, 30))
    assert ownership_from_yahoo("X", None, None).top_institutions == ()
