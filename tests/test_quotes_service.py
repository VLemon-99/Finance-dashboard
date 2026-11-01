"""Quote lookup rules: ticker checking, day change maths, saving, and F1.6 / F1.7."""

import pytest
from sqlmodel import select

from app.db.models import Instrument, Quote
from app.services.quotes import InvalidTicker, day_change, lookup_quote, normalize_ticker


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("AAPL", "AAPL"), ("  aapl ", "AAPL"), ("brk.b", "BRK-B"), ("BRK-B", "BRK-B"), ("F", "F")],
)
def test_normalize_ticker_accepts_ticker_shapes(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "1ABC", "AAPL;DROP", "<script>", "TOOLONGTICKER", "AA PL", "A--B", "../etc"],
)
def test_normalize_ticker_rejects_everything_else(raw):
    with pytest.raises(InvalidTicker):
        normalize_ticker(raw)


def test_day_change_known_answer():
    change, pct = day_change(259.58, 258.04)
    assert change == pytest.approx(1.54)
    assert pct == pytest.approx(0.596807, abs=1e-6)


def test_day_change_negative():
    change, pct = day_change(90.0, 100.0)
    assert change == pytest.approx(-10.0)
    assert pct == pytest.approx(-10.0)


@pytest.mark.parametrize("previous_close", [None, 0.0])
def test_day_change_without_previous_close(previous_close):
    assert day_change(100.0, previous_close) == (None, None)


def test_lookup_fetches_and_saves(session, fake_market_data):
    view = lookup_quote(session, fake_market_data, "aapl")
    assert view.status == "ok"
    assert view.symbol == "AAPL"
    assert view.last_price == pytest.approx(259.58)
    assert view.change == pytest.approx(1.54)
    assert view.note is None

    instrument = session.exec(select(Instrument)).one()
    assert (instrument.symbol, instrument.kind) == ("AAPL", "stock")
    quote = session.exec(select(Quote)).one()
    assert quote.instrument_id == instrument.id
    assert quote.last_price == pytest.approx(259.58)
    assert quote.previous_close == pytest.approx(258.04)
    assert quote.source == "yahoo"


def test_second_lookup_updates_the_same_row(session, fake_market_data):
    first = lookup_quote(session, fake_market_data, "AAPL")
    second = lookup_quote(session, fake_market_data, "AAPL")
    assert len(session.exec(select(Quote)).all()) == 1
    assert len(session.exec(select(Instrument)).all()) == 1
    assert second.fetched_at >= first.fetched_at


def test_invalid_ticker_never_calls_the_source(session, fake_market_data):
    with pytest.raises(InvalidTicker):
        lookup_quote(session, fake_market_data, "AAPL; rm -rf /")
    assert fake_market_data.calls == []


def test_f1_7_unknown_ticker_is_not_found_and_not_saved(session, fake_market_data):
    fake_market_data.mode = "not_found"
    view = lookup_quote(session, fake_market_data, "ZZZZ")
    assert view.status == "not_found"
    assert "not found" in view.note
    assert session.exec(select(Instrument)).all() == []


def test_f1_6_source_down_shows_stored_value_as_stale(session, fake_market_data):
    saved = lookup_quote(session, fake_market_data, "AAPL")
    fake_market_data.mode = "down"
    view = lookup_quote(session, fake_market_data, "AAPL")
    assert view.status == "stale"
    assert view.last_price == pytest.approx(259.58)
    assert view.change == pytest.approx(1.54)
    assert view.fetched_at == saved.fetched_at
    assert "did not respond" in view.note


def test_f1_6_source_down_and_nothing_stored(session, fake_market_data):
    fake_market_data.mode = "down"
    view = lookup_quote(session, fake_market_data, "AAPL")
    assert view.status == "error"
    assert view.last_price is None
    assert "No stored data" in view.note


def test_f1_6_unexpected_crash_in_source_is_contained(session, fake_market_data):
    lookup_quote(session, fake_market_data, "AAPL")
    fake_market_data.mode = "crash"
    view = lookup_quote(session, fake_market_data, "AAPL")
    assert view.status == "stale"
    assert view.last_price == pytest.approx(259.58)


def test_previously_known_ticker_returning_nothing_is_stale_not_not_found(
    session, fake_market_data
):
    # yfinance sometimes reports "no data" when Yahoo is struggling; a ticker we had
    # data for a moment ago is more likely a source problem than a vanished company.
    lookup_quote(session, fake_market_data, "AAPL")
    fake_market_data.mode = "not_found"
    view = lookup_quote(session, fake_market_data, "AAPL")
    assert view.status == "stale"
    assert view.last_price == pytest.approx(259.58)
