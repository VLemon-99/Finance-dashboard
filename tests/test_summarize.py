"""Summarizing stored items with the (fake) AI (SPEC F4.6, F4.13)."""

from datetime import timedelta

import pytest
from sqlmodel import select

from app.config import AIConfig, EdgarConfig, NewsConfig, ReportsConfig
from app.db.models import SourceItemRow
from app.services.collect import collect_for_ticker
from app.services.summarize import batches, pending_items, summarize_pending
from app.sources.edgar import EdgarSource
from app.sources.news_rss import GoogleNewsSource, YahooNewsSource
from app.sources.registry import SourceSet

UA = "Test Person test.person@mail.test"


@pytest.fixture
def since(sources_now):
    return sources_now - timedelta(days=7)


@pytest.fixture
def collected(session, fake_fetch, since):
    edgar = EdgarSource(fake_fetch, EdgarConfig(), UA)
    news = NewsConfig()
    sources = SourceSet(
        [GoogleNewsSource(fake_fetch, news), YahooNewsSource(fake_fetch, news), edgar], edgar
    )
    collect_for_ticker(session, sources, "AAPL", since, ReportsConfig())


def all_rows(session):
    return list(session.exec(select(SourceItemRow).order_by(SourceItemRow.id)).all())


def test_pending_is_one_item_per_story_without_form4s(session, collected, since):
    pending = pending_items(session, "AAPL", since)
    titles = [r.title for r in pending]
    # 11 stored: CNBC copy of the Reuters story and both Form 4s are left out.
    assert len(pending) == 8
    assert not any(r.form == "4" for r in pending)
    assert (
        sum("record September" in t.lower() or "record september" in t.lower() for t in titles) == 1
    )


def test_summaries_are_saved(session, collected, since, fake_llm):
    notes = summarize_pending(session, fake_llm, "AAPL", "Apple", since, AIConfig())
    assert notes == []
    reuters = next(r for r in all_rows(session) if r.outlet == "Reuters")
    assert reuters.summary.startswith("Summary of: Apple posts record")
    assert reuters.sentiment == "bullish"
    assert reuters.sentiment_reason == "Reason for bullish."
    assert reuters.is_rumor is False
    assert reuters.ai_model == "fake-model"
    rumor = next(r for r in all_rows(session) if r.outlet == "Example Rumor Blog")
    assert rumor.is_rumor is True
    assert pending_items(session, "AAPL", since) == []


def test_nothing_is_sent_twice(session, collected, since, fake_llm):
    summarize_pending(session, fake_llm, "AAPL", "Apple", since, AIConfig())
    calls = len(fake_llm.item_calls)
    summarize_pending(session, fake_llm, "AAPL", "Apple", since, AIConfig())
    assert len(fake_llm.item_calls) == calls


def test_skipped_items_are_marked_and_not_sent_again(session, collected, since, fake_llm):
    rumor = next(r for r in all_rows(session) if r.outlet == "Example Rumor Blog")
    fake_llm.skip_refs.add(rumor.id)
    summarize_pending(session, fake_llm, "AAPL", "Apple", since, AIConfig())
    session.refresh(rumor)
    assert rumor.summarized_at is not None and rumor.summary is None
    assert pending_items(session, "AAPL", since) == []


def test_ai_failure_leaves_items_pending_with_a_note(session, collected, since, fake_llm):
    fake_llm.fail = "The AI service's usage limit was reached; try again later."
    notes = summarize_pending(session, fake_llm, "AAPL", "Apple", since, AIConfig())
    assert notes == ["AI summaries: The AI service's usage limit was reached; try again later."]
    assert len(pending_items(session, "AAPL", since)) == 8


def test_only_public_content_is_sent(session, collected, since, fake_llm):
    summarize_pending(session, fake_llm, "AAPL", "Apple", since, AIConfig())
    for ticker, company, items in fake_llm.item_calls:
        assert (ticker, company) == ("AAPL", "Apple")
        for item in items:
            assert set(vars(item)) == {"ref", "kind", "outlet", "title", "published", "text"}


def test_batches_respect_item_and_size_limits():
    rows = [SourceItemRow(title="t", text="x" * 6_000) for _ in range(5)]
    by_count = batches(rows, AIConfig(max_items_per_call=2))
    assert [len(b) for b in by_count] == [2, 2, 1]
    by_size = batches(rows, AIConfig(max_chars_per_call=12_010))
    assert [len(b) for b in by_size] == [2, 2, 1]
    huge = [SourceItemRow(title="t", text="x" * 50_000)] + rows[:1]
    assert [len(b) for b in batches(huge, AIConfig(max_chars_per_call=10_000))] == [1, 1]
