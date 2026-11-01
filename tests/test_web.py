"""The quote lookup page end to end (fake Yahoo with the recorded sample, temporary database)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import Quote, User
from app.db.session import make_engine
from app.main import create_app
from app.timeutils import format_eastern
from app.web.formatting import session_label


def age_stored_quotes(engine, minutes=16):
    """Make stored quotes older than the 15-minute refresh window."""
    with Session(engine) as session:
        for quote in session.exec(select(Quote)):
            quote.fetched_at = quote.fetched_at - timedelta(minutes=minutes)
            session.add(quote)
        session.commit()


def test_lookup_page_shows_the_form(client):
    response = client.get("/lookup")
    assert response.status_code == 200
    assert '<form action="/lookup"' in response.text
    assert 'name="ticker"' in response.text
    assert "htmx.min.js" in response.text


def test_static_files_are_served(client):
    assert client.get("/static/htmx.min.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_quote_flow_full_page(client, engine):
    response = client.get("/lookup", params={"ticker": "aapl"})
    assert response.status_code == 200
    html = response.text
    assert "AAPL" in html
    assert "259.58" in html
    # The sample's last session is Fri Oct 30, so the change is labelled with that day.
    assert f"+1.54 (+0.60%) {session_label(date(2026, 10, 30), date(2026, 11, 1))}" in html
    assert "on Fri, Oct 30" in html
    assert "257.60 – 260.15" in html  # day range
    assert "186.20 – 271.40" in html  # 52-week range
    assert "$3.85T" in html
    assert 'class="change up"' in html
    with Session(engine) as session:
        stored = session.exec(select(Quote)).one()
        assert f"Fetched {format_eastern(stored.fetched_at)}" in html
    assert " ET" in html


def test_quote_flow_htmx_partial(client):
    response = client.get(
        "/lookup/result", params={"ticker": "AAPL"}, headers={"HX-Request": "true"}
    )
    assert response.status_code == 200
    assert "259.58" in response.text
    assert "<html" not in response.text  # just the result box


def test_recent_quote_is_reused_on_lookup(client, fake_market_data):
    client.get("/lookup", params={"ticker": "AAPL"})
    client.get("/lookup", params={"ticker": "AAPL"})
    assert fake_market_data.calls == ["AAPL"]


def test_f1_6_page_still_loads_when_source_is_down(client, engine, fake_market_data):
    client.get("/lookup", params={"ticker": "AAPL"})
    age_stored_quotes(engine)
    fake_market_data.mode = "down"
    response = client.get("/lookup", params={"ticker": "AAPL"})
    assert response.status_code == 200
    assert "259.58" in response.text
    assert 'data-status="stale"' in response.text
    assert ">stale</span>" in response.text
    assert "did not respond" in response.text


def test_f1_6_source_down_with_nothing_stored_shows_error_note(client, fake_market_data):
    fake_market_data.mode = "down"
    response = client.get("/lookup/result", params={"ticker": "AAPL"})
    assert response.status_code == 200
    assert "No stored data for AAPL yet" in response.text


def test_f1_7_unknown_ticker(client, fake_market_data):
    fake_market_data.mode = "not_found"
    response = client.get("/lookup", params={"ticker": "ZZZZ"})
    assert response.status_code == 200
    assert "Ticker ZZZZ not found." in response.text


@pytest.mark.parametrize("bad", ["<script>alert(1)</script>", "1234", "A B C"])
def test_invalid_input_is_rejected_politely(client, fake_market_data, bad):
    response = client.get("/lookup", params={"ticker": bad})
    assert response.status_code == 200
    assert "Please enter a valid US ticker" in response.text
    assert "<script>alert(1)</script>" not in response.text  # never echoed back unescaped
    assert fake_market_data.calls == []


def test_f7_1_default_user_created_once_at_startup(engine, fake_market_data):
    for _ in range(2):  # start the app twice
        with TestClient(create_app(engine=engine, market_data=fake_market_data)):
            pass
    with Session(engine) as session:
        users = session.exec(select(User)).all()
    assert [u.email for u in users] == ["owner@localhost"]


def test_startup_explains_missing_database_setup(tmp_path, fake_market_data):
    engine = make_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    app = create_app(engine=engine, market_data=fake_market_data)
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        with TestClient(app):
            pass


def test_times_are_shown_in_us_eastern():
    # Standard time (after the Nov 1, 2026 clock change) and daylight time.
    assert format_eastern(datetime(2026, 11, 3, 15, 15, tzinfo=UTC)) == "Nov 3, 2026 10:15 AM ET"
    assert format_eastern(datetime(2026, 7, 1, 14, 30, tzinfo=UTC)) == "Jul 1, 2026 10:30 AM ET"
    # Times read back from SQLite have no timezone attached; they are UTC.
    assert format_eastern(datetime(2026, 7, 1, 20, 5)) == "Jul 1, 2026 4:05 PM ET"
