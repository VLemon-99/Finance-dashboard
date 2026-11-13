"""Report pages: "Generate now", a run's progress and results, one report, and history (F4.9,
F4.10). Reports are only read here; they're built in `services/reports.py`.
"""

from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlmodel import Session, col, func, select

from app.db.models import Report, ReportRun
from app.scheduler import next_scheduled_run
from app.services import watchlists as wl
from app.services.reports import running_run, start_run
from app.tickers import InvalidTicker, normalize_ticker
from app.timeutils import EASTERN, as_utc, today_eastern, utc_now
from app.web.report_view import glance
from app.web.routes import SessionDep, UserDep, _redirect, templates

router = APIRouter()

HISTORY_LIMIT = 100


def reports_left_today(request: Request, session: Session, user_id: int) -> int | None:
    """How many more reports this user may start today (F7.8); None = no limit (the owner).

    Days are US Eastern days, counted from the runs' start times.
    """
    if user_id == request.app.state.user_id:
        return None
    limit = request.app.state.accounts.reports_per_day
    today = utc_now().astimezone(EASTERN).date()
    start = datetime(today.year, today.month, today.day, tzinfo=EASTERN)
    used = session.exec(
        select(func.count())
        .select_from(ReportRun)
        .where(ReportRun.user_id == user_id, ReportRun.started_at >= start)
    ).one()
    return max(0, limit - used)


def _used_up_message(request: Request) -> str:
    limit = request.app.state.accounts.reports_per_day
    reports = "report" if limit == 1 else "reports"
    return (
        f"You have used all {limit} {reports} for today. "
        "You can generate more tomorrow (US Eastern time)."
    )


class ReportNotFound(LookupError):
    """A report or run that doesn't exist, or isn't yours."""


async def report_not_found(request: Request, exc: Exception) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "not_found.html", {"what": "report"}, status_code=404
    )


def _history_page(
    request: Request,
    session: Session,
    user_id: int,
    ticker: str = "",
    day: date | None = None,
    error: str | None = None,
    status_code: int = 200,
):
    runs = select(ReportRun).where(ReportRun.user_id == user_id)
    reports = select(Report).where(Report.user_id == user_id)
    if day is not None:
        start = datetime(day.year, day.month, day.day, tzinfo=EASTERN)
        end = start + timedelta(days=1)
        runs = runs.where(ReportRun.started_at >= start, ReportRun.started_at < end)
        reports = reports.where(Report.generated_at >= start, Report.generated_at < end)
    if ticker:
        reports = reports.where(Report.ticker == ticker)
    return templates.TemplateResponse(
        request,
        "reports.html",
        {
            "runs": session.exec(
                runs.order_by(col(ReportRun.started_at).desc()).limit(HISTORY_LIMIT)
            ).all(),
            "reports": session.exec(
                reports.order_by(col(Report.generated_at).desc()).limit(HISTORY_LIMIT)
            ).all()
            if ticker
            else [],
            "watchlists": wl.list_watchlists(session, user_id),
            "ticker": ticker,
            "day": day,
            "today": today_eastern(),
            "schedule": _schedule_info(request),
            "reports_left": reports_left_today(request, session, user_id),
            "reports_per_day": request.app.state.accounts.reports_per_day,
            "error": error,
        },
        status_code=status_code,
    )


def _schedule_info(request: Request) -> dict[str, Any]:
    """For the "Automatic reports" line on the Reports page (F4.9)."""
    config = request.app.state.config.schedule
    running = request.app.state.scheduler is not None
    return {
        "on": running,
        "time": config.time,
        "next": next_scheduled_run(utc_now(), config.time) if running else None,
    }


@router.get("/reports", response_class=HTMLResponse)
def reports_page(
    request: Request,
    session: SessionDep,
    user_id: UserDep,
    ticker: str = "",
    day: str = "",
):
    """History, filterable by ticker (`?ticker=AAPL`) and by date (`?day=2026-11-02`)."""
    error = None
    clean_ticker = ""
    picked_day = None
    if ticker.strip():
        try:
            clean_ticker = normalize_ticker(ticker)
        except InvalidTicker:
            error = "Please enter a valid US ticker, e.g. AAPL or BRK-B."
    if day.strip():
        try:
            picked_day = date.fromisoformat(day.strip())
        except ValueError:
            error = "Please pick a valid date."
    return _history_page(
        request,
        session,
        user_id,
        clean_ticker,
        picked_day,
        error=error,
        status_code=400 if error else 200,
    )


@router.post("/reports/generate", response_class=HTMLResponse)
def generate_report(
    request: Request,
    session: SessionDep,
    user_id: UserDep,
    watchlist_id: Annotated[int | None, Form()] = None,
    ticker: Annotated[str, Form(max_length=50)] = "",
):
    """The "Generate now" button: for a whole watchlist, or one ticker (F4.9)."""
    already = running_run(session, user_id)
    if already is not None:
        return _redirect(f"/reports/runs/{already.id}")  # one run at a time
    if reports_left_today(request, session, user_id) == 0:
        return _history_page(
            request, session, user_id, error=_used_up_message(request), status_code=429
        )
    if watchlist_id is not None:
        watchlist = wl.get_watchlist(session, user_id, watchlist_id)  # 404 if not yours
        tickers = wl.watchlist_tickers(session, user_id, watchlist_id)
        label = watchlist.name
        if not tickers:
            return _history_page(
                request,
                session,
                user_id,
                error=f'"{watchlist.name}" is empty. Add stocks to it first.',
                status_code=400,
            )
    else:
        try:
            tickers = [normalize_ticker(ticker)]
        except InvalidTicker:
            return _history_page(
                request,
                session,
                user_id,
                error="Please enter a valid US ticker, e.g. AAPL or BRK-B.",
                status_code=400,
            )
        label = tickers[0]
    run = start_run(session, user_id, tickers, label=label, watchlist_id=watchlist_id)
    state = request.app.state
    state.report_runner(state.engine, run.id, tickers, state.report_services)
    return _redirect(f"/reports/runs/{run.id}")


def _get_run(session: Session, user_id: int, run_id: int) -> ReportRun:
    run = session.get(ReportRun, run_id)
    if run is None or run.user_id != user_id:
        raise ReportNotFound(run_id)
    return run


@router.get("/reports/runs/{run_id}", response_class=HTMLResponse)
def run_page(request: Request, run_id: int, session: SessionDep, user_id: UserDep):
    """A run's progress while it works; afterwards the summary and every ticker's report."""
    run = _get_run(session, user_id, run_id)
    reports = session.exec(
        select(Report).where(Report.run_id == run.id).order_by(col(Report.id))
    ).all()
    return templates.TemplateResponse(request, "report_run.html", {"run": run, "reports": reports})


@router.get("/reports/runs/{run_id}/progress", response_class=HTMLResponse)
def run_progress(request: Request, run_id: int, session: SessionDep, user_id: UserDep):
    """The progress box, polled by the page. When the run ends, the page reloads itself."""
    run = _get_run(session, user_id, run_id)
    if run.status != "running":
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    return templates.TemplateResponse(request, "_run_progress.html", {"run": run})


@router.get("/reports/{report_id}", response_class=HTMLResponse)
def report_page(request: Request, report_id: int, session: SessionDep, user_id: UserDep):
    report = session.get(Report, report_id)
    if report is None or report.user_id != user_id:
        raise ReportNotFound(report_id)
    run = session.get(ReportRun, report.run_id)
    return templates.TemplateResponse(request, "report.html", {"report": report, "run": run})


# --- helpers for the templates -----------------------------------------------------------


def citation_numbers(stories: list[dict[str, Any]]) -> dict[int, int]:
    """Story ref -> the number shown in the text, e.g. [3], matching the Sources list."""
    return {story["ref"]: number for number, story in enumerate(stories, start=1)}


def iso_eastern(value: str | None) -> datetime | None:
    """Stored ISO text -> a UTC datetime, for the `eastern` filter."""
    return as_utc(datetime.fromisoformat(value)) if value else None


templates.env.globals["citation_numbers"] = citation_numbers
templates.env.globals["report_glance"] = glance
templates.env.filters["from_iso"] = iso_eastern
# How a followed fund's change in a stock is described (SPEC F4.8).
templates.env.globals["fund_status"] = {
    "new": "Bought (new position)",
    "added": "Added",
    "reduced": "Reduced",
    "unchanged": "Held, unchanged",
    "sold": "Sold all",
    "held": "Holds",
}
