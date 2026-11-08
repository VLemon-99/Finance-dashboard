"""The macro dashboard page (SPEC F6): one card per indicator from config/macro.yaml, grouped
in sections, with a chart over 1 year or the longest period (5 years by default). Figures are
put together in `services/macro.py`; this page never waits for a source.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.services import macro
from app.timeutils import utc_now
from app.web.routes import SessionDep, templates

router = APIRouter()


@router.get("/macro", response_class=HTMLResponse)
def macro_page(request: Request, session: SessionDep, years: str = ""):
    setup: macro.MacroSetup = request.app.state.macro
    longest = setup.config.history_years
    span = 1 if years == "1" else longest  # anything else: the longest chart
    running = macro.start_refresh(request.app.state.engine, setup, utc_now())
    first_time = macro.never_fetched(session, setup)
    session.expire_all()  # a refresh that just ran used its own database session
    return templates.TemplateResponse(
        request,
        "macro.html",
        {
            "groups": macro.indicator_views(session, setup, span),
            "span": span,
            "longest": longest,
            "running": running,
            "run_note": macro.run_note(running, first_time),
            "fred_missing": not setup.fred.is_set_up,
        },
    )
