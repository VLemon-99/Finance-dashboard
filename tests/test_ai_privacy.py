"""Nothing personal reaches the AI in any step of a report (SPEC F4.13; improvement IMP-2).

The owner's watchlist (its name and other stocks), portfolio trades (symbols, quantities,
prices) and the run's label are planted with values that appear nowhere in the sample
content. A full report runs with news, SEC filings, a blog, a YouTube channel and market
facts; every call to the (fake) AI is recorded and searched for those values.
"""

from datetime import date

import pytest
from sqlmodel import select

from app.config import BlogFeed, SourcesConfig, YouTubeChannel
from app.db.models import Instrument, Report, ReportRun, Transaction, Watchlist, WatchlistItem
from app.services.reports import ReportServices, execute_run, start_run
from app.services.users import ensure_default_user
from app.sources.edgar import EdgarSource
from app.sources.news_rss import GoogleNewsSource, YahooNewsSource
from app.sources.registry import SourceSet, blog_and_video_sources
from tests.conftest import TEST_SEC_USER_AGENT

WATCHLIST_NAME = "Zebra Retirement Pot"
OTHER_STOCK = "QZXW"  # in the watchlist and the portfolio, but not reported on
OPTION = "AAPL261218P00123000"  # an AAPL put the owner sold
PERSONAL = [
    WATCHLIST_NAME,
    "Zebra",
    OTHER_STOCK,
    OPTION,
    "7313",  # shares of QZXW bought
    "1234.56",  # their price
    "4271",  # put contracts sold
    "6.4817",  # the put's premium
    "987.65",  # the stock price when the put was sold
]


@pytest.fixture
def user_id(session):
    return ensure_default_user(session, "owner@localhost").id


@pytest.fixture
def services(fake_fetch, fake_llm, sources_now, app_config, market_facts):
    sources_config = SourcesConfig.model_validate(
        {
            "blogs": {
                "feeds": [BlogFeed(name="Sample Tech Blog", feed="https://blog.example.com/feed/")]
            },
            "youtube": {
                "channels": [
                    YouTubeChannel(name="Sample Markets", channel_id="UCsampleChannelAAAAAAAAA")
                ]
            },
        }
    )
    edgar = EdgarSource(fake_fetch, sources_config.sec_edgar, TEST_SEC_USER_AGENT)
    news = sources_config.news
    sources = [GoogleNewsSource(fake_fetch, news), YahooNewsSource(fake_fetch, news), edgar]
    return ReportServices(
        sources=SourceSet(sources + blog_and_video_sources(fake_fetch, sources_config), edgar),
        llm=fake_llm,
        llm_problem=None,
        config=app_config,
        sources_config=sources_config,
        facts=market_facts,
    )


def plant_personal_data(session, user_id) -> Watchlist:
    aapl = Instrument(kind="stock", symbol="AAPL")
    other = Instrument(kind="stock", symbol=OTHER_STOCK)
    put = Instrument(
        kind="option",
        symbol=OPTION,
        underlying="AAPL",
        option_type="put",
        strike=123.0,
        expiration=date(2026, 12, 18),
    )
    watchlist = Watchlist(user_id=user_id, name=WATCHLIST_NAME)
    session.add_all([aapl, other, put, watchlist])
    session.commit()
    session.add_all(
        [
            WatchlistItem(user_id=user_id, watchlist_id=watchlist.id, instrument_id=aapl.id),
            WatchlistItem(user_id=user_id, watchlist_id=watchlist.id, instrument_id=other.id),
            Transaction(
                user_id=user_id,
                instrument_id=other.id,
                side="bought",
                quantity=7313,
                price=1234.56,
                trade_date=date(2026, 10, 1),
                price_source="manual",
            ),
            Transaction(
                user_id=user_id,
                instrument_id=put.id,
                side="sold",
                quantity=4271,
                price=6.4817,
                trade_date=date(2026, 10, 2),
                price_source="manual",
                underlying_price=987.65,
            ),
        ]
    )
    session.commit()
    return watchlist


def test_no_personal_data_in_any_ai_call(session, engine, user_id, services, fake_llm):
    watchlist = plant_personal_data(session, user_id)
    run = start_run(session, user_id, ["AAPL"], label=watchlist.name, watchlist_id=watchlist.id)
    execute_run(engine, run.id, ["AAPL"], services)
    session.expire_all()
    run = session.get(ReportRun, run.id)
    assert (run.status, run.done) == ("done", 1)
    assert session.exec(select(Report).where(Report.run_id == run.id)).one()

    # Every AI step was really used, so the check below can't pass by sending nothing.
    assert fake_llm.item_calls, "no items were summarized"
    assert fake_llm.video_calls, "no video was watched"
    assert fake_llm.report_calls, "no report statements were written"

    sent = repr(fake_llm.item_calls) + repr(fake_llm.video_calls) + repr(fake_llm.report_calls)
    assert "Apple" in sent  # the public content did go
    leaks = [value for value in PERSONAL if value in sent]
    assert leaks == []
