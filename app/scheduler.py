"""Automatic weekday reports (SPEC F4.9), using APScheduler (DECISIONS: tech stack).

Off unless `schedule.enabled` is true in config/settings.yaml (owner 2026-11-04: reports
are started by hand for now). When on, every weekday at `schedule.time` US Eastern, while
the app is running, each watchlist gets a report run, one after another. If the computer
was asleep or the app was closed at that time, the run is made only if the app is back
within 30 minutes; otherwise that day is skipped.
"""

import logging
from datetime import UTC, datetime, time, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import Engine
from sqlmodel import Session

from app.config import ScheduleConfig
from app.services import watchlists as wl
from app.services.reports import ReportServices, execute_run, running_run, start_run
from app.timeutils import EASTERN, as_utc

log = logging.getLogger(__name__)

JOB_ID = "weekday-reports"
LATE_START_SECONDS = 30 * 60


def next_scheduled_run(now: datetime, at: time) -> datetime:
    """The next weekday at `at` US Eastern, after `now`. Returned in UTC."""
    local = as_utc(now).astimezone(EASTERN)
    candidate = datetime.combine(local.date(), at, tzinfo=EASTERN)
    if candidate <= local:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:  # Saturday, Sunday
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


def run_scheduled_reports(engine: Engine, user_id: int, services: ReportServices) -> list[int]:
    """One run per watchlist that has stocks, in the home page's order, one after another.
    Returns the run ids."""
    run_ids = []
    with Session(engine) as session:
        lists = [(w.id, w.name) for w in wl.list_watchlists(session, user_id)]
    for watchlist_id, name in lists:
        with Session(engine) as session:
            if running_run(session, user_id) is not None:
                log.info("Scheduled report for %s skipped: another run is going", name)
                continue
            tickers = wl.watchlist_tickers(session, user_id, watchlist_id)
            if not tickers:
                continue
            run = start_run(session, user_id, tickers, label=name, watchlist_id=watchlist_id)
            run_id = run.id
        execute_run(engine, run_id, tickers, services)
        run_ids.append(run_id)
    return run_ids


def start_scheduler(
    engine: Engine, user_id: int, services: ReportServices, config: ScheduleConfig
) -> BackgroundScheduler | None:
    """Start the weekday job, if switched on. Returns the scheduler (to stop it later)."""
    if not config.enabled:
        return None
    scheduler = BackgroundScheduler(timezone=EASTERN)
    scheduler.add_job(
        run_scheduled_reports,
        CronTrigger(
            day_of_week="mon-fri",
            hour=config.time.hour,
            minute=config.time.minute,
            timezone=EASTERN,
        ),
        args=[engine, user_id, services],
        id=JOB_ID,
        misfire_grace_time=LATE_START_SECONDS,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    log.info("Automatic reports on: weekdays at %s US Eastern", config.time.strftime("%H:%M"))
    return scheduler
