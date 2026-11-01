"""Web pages."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db.session import get_session
from app.providers.market_data import MarketData
from app.services.quotes import InvalidTicker, QuoteView, lookup_quote
from app.timeutils import format_eastern

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["eastern"] = format_eastern

router = APIRouter()


def get_market_data(request: Request) -> MarketData:
    return request.app.state.market_data


SessionDep = Annotated[Session, Depends(get_session)]
MarketDataDep = Annotated[MarketData, Depends(get_market_data)]


def _lookup(
    session: Session, market_data: MarketData, ticker: str
) -> tuple[QuoteView | None, str | None]:
    """Returns (quote, input_error)."""
    try:
        return lookup_quote(session, market_data, ticker), None
    except InvalidTicker:
        return None, "Please enter a valid US ticker, e.g. AAPL or BRK-B."


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    session: SessionDep,
    market_data: MarketDataDep,
    ticker: str = "",
):
    """The quote page. Works without JavaScript too (plain form submit to this page)."""
    quote, input_error = _lookup(session, market_data, ticker) if ticker.strip() else (None, None)
    return templates.TemplateResponse(
        request,
        "index.html",
        {"ticker": ticker.strip(), "quote": quote, "input_error": input_error},
    )


@router.get("/quote", response_class=HTMLResponse)
def quote_partial(
    request: Request,
    session: SessionDep,
    market_data: MarketDataDep,
    ticker: str = "",
):
    """Just the result box; the page swaps it in with HTMX."""
    quote, input_error = _lookup(session, market_data, ticker)
    return templates.TemplateResponse(
        request, "_quote_result.html", {"quote": quote, "input_error": input_error}
    )
