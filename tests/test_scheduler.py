"""Automatic weekday reports (SPEC F4.9). Off by default (owner 2026-11-04)."""

from datetime import UTC, datetime, time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import PROJECT_ROOT, AppConfig, load_app_config
from app.db.models import Report, ReportRun
from app.main import create_app
from app.scheduler import JOB_ID, next_scheduled_run, run_scheduled_reports
from app.services.reports import start_run
from app.services.users import ensure_default_user
from tests.test_web_watchlists import add, create

AT = time(7, 30)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # Mon Nov 2, 2026, 7:00 AM ET (standard time, UTC-5) -> same morning, 7:30 AM ET.
        (datetime(2026, 11, 2, 12, 0, tzinfo=UTC), datetime(2026, 11, 2, 12, 30, tzinfo=UTC)),
        # Exactly 7:30 -> the next weekday.
        (datetime(2026, 11, 2, 12, 30, tzinfo=UTC), datetime(2026, 11, 3, 12, 30, tzinfo=UTC)),
        # Friday after 7:30 -> Monday; Saturday -> Monday.
        (datetime(2026, 11, 6, 15, 0, tzinfo=UTC), datetime(2026, 11, 9, 12, 30, tzinfo=UTC)),
        (datetime(2026, 11, 7, 12, 0, tzinfo=UTC), datetime(2026, 11, 9, 12, 30, tzinfo=UTC)),
        # Over the clock change (Nov 1, 2026): Friday Oct 30 is daylight time (UTC-4), the
        # next run on Monday Nov 2 is 7:30 standard time, 12:30 UTC (not 11:30).
        (datetime(2026, 10, 30, 13, 0, tzinfo=UTC), datetime(2026, 11, 2, 12, 30, tzinfo=UTC)),
        # Sunday 11 PM ET is already Monday in UTC: still Monday 7:30 AM ET.
        (datetime(2026, 11, 9, 4, 0, tzinfo=UTC), datetime(2026, 11, 9, 12, 30, tzinfo=UTC)),
    ],
)
def test_next_scheduled_run(now, expected):
    assert next_scheduled_run(now, AT) == expected


def test_off_by_default():
    assert AppConfig().schedule.enabled is False
    real = load_app_config(PROJECT_ROOT / "config" / "settings.yaml")
    assert real.schedule.enabled is False
    assert real.schedule.time == AT


def test_reports_page_says_automatic_reports_are_off(client):
    html = client.get("/reports").text
    assert "Automatic reports are off." in html
    assert "weekdays at 7:30 AM ET" in html
    assert client.app.state.scheduler is None


def test_every_watchlist_gets_a_run_one_after_another(client, engine, report_services):
    add(client, create(client, "Tech"), "AAPL")
    create(client, "Empty")  # nothing to report on: skipped
    add(client, create(client, "Other"), "MSFT")
    with Session(engine) as session:
        user_id = ensure_default_user(session, "owner@localhost").id
    run_ids = run_scheduled_reports(engine, user_id, report_services)
    with Session(engine) as session:
        runs = [session.get(ReportRun, i) for i in run_ids]
        assert [(r.label, r.status) for r in runs] == [("Tech", "done"), ("Other", "done")]
        assert {r.ticker for r in session.exec(select(Report)).all()} == {"AAPL", "MSFT"}


def test_skipped_while_another_run_is_going(client, engine, report_services):
    add(client, create(client, "Tech"), "AAPL")
    with Session(engine) as session:
        user_id = ensure_default_user(session, "owner@localhost").id
        start_run(session, user_id, ["AAPL"], label="By hand")  # still running
    assert run_scheduled_reports(engine, user_id, report_services) == []


def test_switched_on_the_job_runs_on_weekdays_and_stops_with_the_app(
    engine, fake_market_data, report_services, report_runner
):
    config = AppConfig.model_validate({"schedule": {"enabled": True, "time": "08:05"}})
    app = create_app(
        engine=engine,
        market_data=fake_market_data,
        app_config=config,
        report_services=report_services,
        report_runner=report_runner,
    )
    with TestClient(app) as client:
        scheduler = app.state.scheduler
        job = scheduler.get_job(JOB_ID)
        expected = next_scheduled_run(datetime.now(UTC), time(8, 5))
        assert job.next_run_time.astimezone(UTC) == expected
        assert str(job.trigger.fields[4]) == "mon-fri"  # day of week
        html = client.get("/reports").text
        assert "Automatic reports: <strong>on</strong>" in html
        assert "every weekday at 8:05 AM ET" in html
    assert not scheduler.running


def test_schedule_time_is_checked():
    with pytest.raises(ValueError):
        AppConfig.model_validate({"schedule": {"time": "7.30am"}})
