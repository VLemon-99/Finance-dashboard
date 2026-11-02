"""Insider buys and sells (SPEC F4.8): known answers from the Form 4 samples."""

import pytest

from app.db.models import SourceItemRow
from app.services.insider import insider_summary
from app.sources.edgar import parse_form4
from tests.conftest import SOURCE_FIXTURES


def form4_row(ref: int, name: str) -> SourceItemRow:
    data = parse_form4((SOURCE_FIXTURES / name).read_bytes())
    return SourceItemRow(id=ref, form="4", data=data)


def test_totals_from_the_samples():
    summary = insider_summary(
        [form4_row(1, "sec_form4_sell.xml"), form4_row(2, "sec_form4_buy.xml")]
    )
    assert (summary.buys, summary.sells) == (1, 2)
    assert summary.shares_sold == 15_000
    assert summary.shares_bought == 1_000
    # 12,000 x 250.25 + 3,000 x 251.50 = 3,003,000 + 754,500
    assert summary.value_sold == pytest.approx(3_757_500.0)
    assert summary.value_bought == pytest.approx(230_000.0)
    assert summary.net_value == pytest.approx(-3_527_500.0)
    assert summary.missing_prices == 0
    assert [t.date for t in summary.trades] == ["2026-10-30", "2026-10-29", "2026-09-12"]
    newest = summary.trades[0]
    assert (newest.ref, newest.name, newest.role) == (
        1,
        "Example Jane Q",
        "Chief Financial Officer",
    )
    assert newest.value == pytest.approx(754_500.0)
    assert newest.plan_10b5_1 is True


def test_trade_without_price_is_listed_but_not_in_dollar_totals():
    row = SourceItemRow(
        id=5,
        form="4",
        data={
            "owners": [{"name": "A", "role": "Director"}],
            "trades": [{"date": "2026-10-01", "kind": "buy", "shares": 100.0, "price": None}],
        },
    )
    summary = insider_summary([row])
    assert summary.shares_bought == 100 and summary.value_bought == 0
    assert summary.missing_prices == 1
    assert summary.trades[0].value is None


def test_filings_with_only_grants_are_counted_separately():
    row = SourceItemRow(id=6, form="4", data={"owners": [], "trades": []})
    summary = insider_summary([row])
    assert summary.trades == [] and summary.filings_without_trades == 1
