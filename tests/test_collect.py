"""Collecting and storing research items (SPEC F4.11, F4.12). Recorded samples, no network."""

from datetime import timedelta

import pytest
from sqlmodel import select

from app.config import EdgarConfig, NewsConfig, ReportsConfig
from app.db.models import SourceItemRow
from app.services.collect import collect_for_ticker, headline_similarity, normalize_headline
from app.sources.edgar import EdgarSource
from app.sources.news_rss import GoogleNewsSource, YahooNewsSource
from app.sources.registry import SourceSet

UA = "Test Person test.person@mail.test"
CONFIG = ReportsConfig()


def make_sources(fetch, user_agent: str = UA) -> SourceSet:
    edgar = EdgarSource(fetch, EdgarConfig(), user_agent)
    news = NewsConfig()
    return SourceSet(
        sources=[GoogleNewsSource(fetch, news), YahooNewsSource(fetch, news), edgar], edgar=edgar
    )


@pytest.fixture
def since(sources_now):
    return sources_now - timedelta(days=7)


def rows(session) -> list[SourceItemRow]:
    return list(session.exec(select(SourceItemRow).order_by(SourceItemRow.id)).all())


def test_collects_and_stores_items_from_every_source(session, fake_fetch, since):
    result = collect_for_ticker(session, make_sources(fake_fetch), "AAPL", since, CONFIG)
    assert result.missing == []
    assert result.new_items == 11  # 4 Google News + 2 Yahoo + 5 SEC filings
    stored = rows(session)
    assert {r.source for r in stored} == {"google_news", "yahoo_news", "sec_edgar"}
    form4 = next(r for r in stored if r.form == "4" and "1730412312" in r.url)
    assert form4.data["trades"][0]["kind"] == "sell"  # enriched before saving


def test_second_run_stores_nothing_new_and_downloads_no_filings_again(session, fake_fetch, since):
    sources = make_sources(fake_fetch)
    collect_for_ticker(session, sources, "AAPL", since, CONFIG)
    downloads = len(fake_fetch.calls)
    again = collect_for_ticker(session, sources, "AAPL", since, CONFIG)
    assert again.new_items == 0
    assert len(rows(session)) == 11
    new_calls = fake_fetch.calls[downloads:]
    assert not any("Archives" in c for c in new_calls)  # no filing re-downloaded


def test_same_story_from_two_outlets_is_one_story(session, fake_fetch, since):
    collect_for_ticker(session, make_sources(fake_fetch), "AAPL", since, CONFIG)
    by_outlet = {r.outlet: r for r in rows(session) if r.source == "google_news"}
    reuters, cnbc = by_outlet["Reuters"], by_outlet["CNBC"]
    assert reuters.story_id == reuters.id  # first one leads the story
    assert cnbc.story_id == reuters.id
    stories = {r.story_id for r in rows(session)}
    assert len(stories) == 10  # 11 items, one pair merged


def test_filings_are_never_merged(session, fake_fetch, since):
    collect_for_ticker(session, make_sources(fake_fetch), "AAPL", since, CONFIG)
    filings = [r for r in rows(session) if r.source_type == "filing"]
    assert all(r.story_id == r.id for r in filings)


def test_one_source_down_others_still_stored(session, fake_fetch, since):
    fake_fetch.down.add("https://news.google.com")
    result = collect_for_ticker(session, make_sources(fake_fetch), "AAPL", since, CONFIG)
    assert result.missing == ["Google News: The site didn't answer in time."]
    assert result.new_items == 7


def test_unexpected_crash_in_a_source_is_contained(session, fake_fetch, since):
    class Broken:
        name, label, source_type = "broken", "Broken source", "news"

        def fetch(self, ticker, company, since):
            raise RuntimeError("bug")

        def enrich(self, item):
            return item

    sources = make_sources(fake_fetch)
    sources.sources.insert(0, Broken())
    result = collect_for_ticker(session, sources, "AAPL", since, CONFIG)
    assert result.missing == ["Broken source: unexpected error (details in the app log)."]
    assert result.new_items == 11


def test_filing_that_cannot_be_downloaded_is_retried_next_time(session, fake_fetch, since):
    form4_xml = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000101/wk-form4"
    fake_fetch.down.add(form4_xml)
    sources = make_sources(fake_fetch)
    first = collect_for_ticker(session, sources, "AAPL", since, CONFIG)
    assert first.new_items == 10
    assert first.missing == [
        "SEC EDGAR filings: 1 item(s) couldn't be downloaded; will retry next time."
    ]
    fake_fetch.down.clear()
    second = collect_for_ticker(session, sources, "AAPL", since, CONFIG)
    assert second.new_items == 1 and second.missing == []


def test_without_sec_contact_news_still_works(session, fake_fetch, since):
    result = collect_for_ticker(session, make_sources(fake_fetch, ""), "AAPL", since, CONFIG)
    assert result.new_items == 6  # news only
    assert len(result.missing) == 1 and "SEC_USER_AGENT" in result.missing[0]
    # No company name from the SEC, so Google News is searched by ticker.
    google = next(c for c in fake_fetch.calls if "news.google.com" in c)
    assert "%22AAPL+stock%22" in google and "Apple" not in google


def test_headline_helpers():
    assert normalize_headline("  Apple Posts Record Quarter!  ") == "apple posts record quarter"
    assert headline_similarity("Apple beats, stock up", "apple beats stock up") == 1.0
    assert headline_similarity("Apple beats estimates", "Tesla recalls cars") < 0.5
