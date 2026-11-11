"""Report history by date and ticker: edge cases (SPEC F4.10; improvement IMP-4).

Times are stored in UTC and the date filter uses US Eastern days, so a report made in the
evening (already the next day in UTC) belongs to its Eastern date.
"""

import re
from datetime import UTC, datetime

import pytest
from sqlmodel import Session, select

from app.db.models import Report, ReportRun, User


@pytest.fixture
def owner_id(client, engine):
    with Session(engine) as session:
        return session.exec(select(User)).one().id


def plant(engine, user_id, ticker, at, label=None):
    """One finished run with one report, made at `at` (UTC)."""
    with Session(engine) as session:
        run = ReportRun(
            user_id=user_id, label=label or ticker, started_at=at, status="done", total=1, done=1
        )
        session.add(run)
        session.commit()
        report = Report(
            user_id=user_id,
            run_id=run.id,
            ticker=ticker,
            generated_at=at,
            period_start=at,
            period_end=at,
            body={},
        )
        session.add(report)
        session.commit()
        return run.id, report.id


def run_links(html):
    return re.findall(r'href="/reports/runs/(\d+)"', html)


def report_links(html):
    return [int(n) for n in re.findall(r'href="/reports/(\d+)"', html)]


@pytest.mark.parametrize(
    ("at", "eastern_day", "utc_day"),
    [
        # 9:30pm EST on Nov 2 is 02:30 UTC on Nov 3.
        (datetime(2026, 11, 3, 2, 30, tzinfo=UTC), "2026-11-02", "2026-11-03"),
        # 11:00pm EDT on Jul 15 (summer time) is 03:00 UTC on Jul 16.
        (datetime(2026, 7, 16, 3, 0, tzinfo=UTC), "2026-07-15", "2026-07-16"),
    ],
)
def test_evening_report_is_listed_under_its_eastern_date(
    client, engine, owner_id, at, eastern_day, utc_day
):
    run_id, report_id = plant(engine, owner_id, "AAPL", at, label="Evening run")
    on_day = client.get("/reports", params={"day": eastern_day}).text
    assert run_links(on_day) == [str(run_id)]
    next_day = client.get("/reports", params={"day": utc_day}).text
    assert run_links(next_day) == [] and "No reports on that date" in next_day
    # With a ticker as well, the report itself is filtered the same way.
    both = client.get("/reports", params={"day": eastern_day, "ticker": "AAPL"}).text
    assert report_links(both) == [report_id]
    both_next = client.get("/reports", params={"day": utc_day, "ticker": "AAPL"}).text
    assert report_links(both_next) == [] and "No AAPL reports on that date" in both_next


def test_eastern_midnight_is_the_boundary(client, engine, owner_id):
    # Nov 3 2026, 04:59 UTC = 11:59pm EST Nov 2; 05:00 UTC = midnight Nov 3.
    late, _ = plant(engine, owner_id, "AAPL", datetime(2026, 11, 3, 4, 59, tzinfo=UTC))
    early, _ = plant(engine, owner_id, "AAPL", datetime(2026, 11, 3, 5, 0, tzinfo=UTC))
    assert run_links(client.get("/reports", params={"day": "2026-11-02"}).text) == [str(late)]
    assert run_links(client.get("/reports", params={"day": "2026-11-03"}).text) == [str(early)]


def test_every_report_of_a_ticker_is_kept_newest_first(client, engine, owner_id):
    ids = [
        plant(engine, owner_id, "MSFT", datetime(2026, 10, day, 15, 0, tzinfo=UTC))[1]
        for day in (28, 30, 29)
    ]
    plant(engine, owner_id, "AAPL", datetime(2026, 10, 31, 15, 0, tzinfo=UTC))
    html = client.get("/reports", params={"ticker": "MSFT"}).text
    assert report_links(html) == [ids[1], ids[2], ids[0]]  # Oct 30, Oct 29, Oct 28


@pytest.mark.parametrize("typed", ["brk.b", "BRK.B", " brk-b "])
def test_ticker_filter_accepts_brk_b_however_typed(client, engine, owner_id, typed):
    _, report_id = plant(engine, owner_id, "BRK-B", datetime(2026, 10, 30, 15, 0, tzinfo=UTC))
    html = client.get("/reports", params={"ticker": typed}).text
    assert "BRK-B reports" in html
    assert report_links(html) == [report_id]
