"""IMP-20: developments and sources that are quicker to scan (SPEC F4.1, F4.2, F4.6, F4.12).

Each statement shows the tone and origin of the stories it cites; a single-outlet story no
longer repeats its title; the sources are counted by kind. Worked out in Python
(`app/web/report_view.py`) from the stored report; nothing is rated anew.
"""

import re

import pytest

from app.web.report_view import claim_origin, claim_tone, source_kinds
from tests.test_web_reports import generate


def story(ref, sentiment=None, kind="news", outlets=("Reuters",), at="2026-10-30T20:45:00+00:00"):
    return {
        "ref": ref,
        "kind": kind,
        "sentiment": sentiment,
        "published_at": at,
        "items": [
            {"outlet": o, "title": f"t{ref}", "url": f"https://example.com/{ref}"} for o in outlets
        ],
    }


STORIES = [
    story(1, "bullish"),
    story(2, "bearish", outlets=("CNBC", "Reuters", "Bloomberg")),
    story(3, "neutral", kind="filing", outlets=("SEC EDGAR",)),
    story(4, None, kind="video", outlets=("Sample Markets",)),
    story(5, "bullish", kind="blog", outlets=("Sample Tech Blog",)),
    story(6, "neutral", kind="post", outlets=("@example",), at="2026-11-01T03:30:00+00:00"),
]


@pytest.mark.parametrize(
    ("refs", "expected"),
    [
        ([1], "bullish"),
        ([1, 5], "bullish"),
        ([2], "bearish"),
        ([1, 2], "mixed"),
        ([1, 2, 3], "mixed"),
        ([3], "neutral"),
        ([1, 3], "neutral"),  # not all bullish
        ([2, 3], "neutral"),
        ([4], None),  # unrated
        ([3, 4], "neutral"),  # unrated stories are left out
        ([], None),
        ([99], None),  # a story the report doesn't have
    ],
)
def test_claim_tone(refs, expected):
    assert claim_tone(refs, STORIES) == expected


def test_claim_origin_names_outlets_and_the_first_storys_day():
    assert claim_origin([1], STORIES) == {"outlets": ["Reuters"], "more": 0, "day": "Oct 30"}
    # Outlets in order, each once, at most two named.
    assert claim_origin([2], STORIES) == {
        "outlets": ["CNBC", "Reuters"],
        "more": 1,
        "day": "Oct 30",
    }
    both = claim_origin([1, 2, 3], STORIES)
    assert (both["outlets"], both["more"]) == (["Reuters", "CNBC"], 2)
    # The day is the US Eastern day: 03:30 UTC on Nov 1 is still Oct 31 in New York.
    assert claim_origin([6], STORIES)["day"] == "Oct 31"
    assert claim_origin([], STORIES) is None and claim_origin([99], STORIES) is None


def test_source_kinds_counts_in_a_fixed_order():
    assert source_kinds(STORIES) == [
        ("news", 2),
        ("filing", 1),
        ("blog post", 1),
        ("video", 1),
        ("X post", 1),
    ]
    assert source_kinds([story(1), story(2, kind="filing"), story(3, kind="filing")]) == [
        ("news", 1),
        ("filings", 2),
    ]
    assert source_kinds([]) == []


# --- on the page -------------------------------------------------------------------------


def section(html, name):
    return html.split(f"<h3>{name}</h3>")[1].split("</section>")[0]


def test_development_lines_show_tone_origin_and_keep_their_citations(client):
    html = section(client.get(generate(client, ticker="AAPL")).text, "Major developments")
    lines = re.findall(r"<li>.*?</li>", html, flags=re.S)
    assert len(lines) == 6
    for line in lines:
        assert re.search(r'<a class="cite" href="#r\d+-s\d+"', line)  # F4.2
        assert 'class="claim-origin"' in line
    beat = next(line for line in lines if "beat estimates" in line)
    assert '<span class="tone bullish" title="Tone of the cited stories">bullish</span>' in beat
    assert "Yahoo Finance · Oct 30" in beat
    record = next(line for line in lines if "record September-quarter revenue" in line)
    assert "Reuters, CNBC · Oct 30" in record  # one story carried by two outlets
    filing = next(line for line in lines if "Form 8-K" in line)
    assert '<span class="tone neutral" title="Tone of the cited stories">neutral</span>' in filing


def test_rumor_line_keeps_its_badge_right_before_the_text(client):
    html = section(client.get(generate(client, ticker="AAPL")).text, "Rumors &amp; speculation")
    assert (
        '<span class="badge">unconfirmed</span> Rumor: Report: Apple said to be exploring' in html
    )
    line = re.search(r"<li>.*?</li>", html, flags=re.S).group(0)
    assert line.index("Rumor: Report") < line.index('class="tone ')
    assert "Example Rumor Blog · Oct 31" in line


def test_single_outlet_story_shows_its_title_once(client):
    html = section(client.get(generate(client, ticker="AAPL")).text, "Sources")
    entries = re.findall(r'<li id="r\d+-s\d+">.*?</li>\s*(?=<li id=|</ol>)', html, flags=re.S)
    assert len(entries) == 8
    single = next(e for e in entries if "beat estimates" in e)
    assert single.count(">Apple results beat estimates on services strength</a>") == 1
    assert "Yahoo Finance, Oct 30, 2026 5:10 PM ET" in single
    multi = next(e for e in entries if "Reuters" in e)
    assert "Reuters, Oct 30, 2026 4:45 PM ET" in multi and "CNBC, Oct 30, 2026 5:02 PM ET" in multi
    assert multi.count("<a href=") == 3  # the heading link and one per outlet (F4.12)


def test_rating_is_a_pill_next_to_the_title_and_the_reason_stays(client):
    html = section(client.get(generate(client, ticker="AAPL")).text, "Sources")
    title = html.split("beat estimates on services strength</a>")[1].split("</p>")[0]
    assert '<span class="rating bullish">bullish</span>' in title
    assert "Reason for bullish." in html


def test_sources_are_counted_by_kind(client):
    html = section(client.get(generate(client, ticker="AAPL")).text, "Sources")
    counts = re.search(r'<p class="source-kinds[^"]*">(.*?)</p>', html, flags=re.S).group(1)
    assert " ".join(counts.split()) == "8 sources: 5 news · 3 filings"
    assert html.count('<span class="badge plain">filing</span>') == 3
    assert html.index('class="source-kinds') < html.index('<ol class="sources">')


def test_report_without_ai_has_no_tone_markers_and_still_shows(client, report_services):
    report_services.llm = None
    report_services.llm_problem = "AI summaries are off: set GEMINI_API_KEY in the .env file."
    response = client.get(generate(client, ticker="AAPL"))
    assert response.status_code == 200
    assert 'class="tone ' not in response.text
    assert 'class="source-kinds' in response.text


def test_claim_origin_is_nothing_when_the_story_names_no_outlet_or_date():
    bare = [{"ref": 1, "items": [{"outlet": None}], "published_at": None}]
    assert claim_origin([1], bare) is None
