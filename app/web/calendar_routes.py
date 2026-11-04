"""The calendar page (SPEC F5.3-F5.5): a list of the next 14 days, or one week, with the
"my tickers only" and "high impact only" filters. Plain links and a GET form, so it works
without JavaScript. Events are put together in `services/calendar.py`.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.services import calendar as cal
from app.timeutils import utc_now
from app.web.routes import SessionDep, UserDep, templates

router = APIRouter()

# The week view can go this far back or ahead (the data doesn't reach further anyway).
MAX_WEEKS_AWAY = 104


def _checked(value: str) -> bool:
    return value in ("1", "on", "true")


@router.get("/calendar", response_class=HTMLResponse)
def calendar_page(
    request: Request,
    session: SessionDep,
    user_id: UserDep,
    view: str = "list",
    week: str = "",
    mine: str = "",
    high: str = "",
):
    setup: cal.CalendarSetup = request.app.state.calendar
    now = utc_now()
    today = cal.eastern_day(now)
    error = None
    view = view if view in ("list", "week") else "list"
    only_mine, only_high = _checked(mine), _checked(high)

    monday = cal.week_start(today)
    if week:
        try:
            picked = cal.week_start(date.fromisoformat(week))
        except ValueError:
            picked, error = monday, "That isn't a date; showing this week."
        if abs((picked - monday).days) > MAX_WEEKS_AWAY * 7:
            picked, error = monday, "That week is too far away; showing this week."
        monday = picked

    tickers, notes = cal.prepare(session, setup, user_id, now)
    cfg = setup.config.calendar
    if view == "week":
        days = [monday + timedelta(days=n) for n in range(7)]
        events = cal.load_events(session, tickers, days[0], days[-1], cfg, setup.fomc)
        groups = cal.by_day(cal.filtered(events, tickers, only_mine, only_high), days)
        start, end = days[0], days[-1]
    else:
        start, end = today, today + timedelta(days=cfg.list_days - 1)
        events = cal.upcoming(cal.load_events(session, tickers, start, end, cfg, setup.fomc), now)
        groups = cal.by_day(cal.filtered(events, tickers, only_mine, only_high))

    return templates.TemplateResponse(
        request,
        "calendar.html",
        {
            "view": view,
            "groups": groups,
            "notes": notes,
            "error": error,
            "mine": only_mine,
            "high": only_high,
            "tickers": tickers,
            "start": start,
            "end": end,
            "today": today,
            "now": now,
            "monday": monday,
            "this_monday": cal.week_start(today),
            "previous_week": monday - timedelta(days=7),
            "next_week": monday + timedelta(days=7),
            "list_days": cfg.list_days,
        },
        status_code=400 if error else 200,
    )
