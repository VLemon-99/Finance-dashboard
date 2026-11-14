"""The "Voices: X and YouTube" section (owner 2026-11-14): what trusted accounts and channels
say, kept apart from news and filings. Built from the stored stories; every entry links to
its numbered source (SPEC F4.2), and nothing is rated anew.
"""

import re

from fastapi.testclient import TestClient

from app.main import create_app
from app.web.report_view import voices
from tests.test_reports_x import with_x
from tests.test_web_reports import generate, with_blog_and_channel


def story(ref, kind, outlet, sentiment=None, **more):
    return {
        "ref": ref,
        "kind": kind,
        "title": f"Title {ref}",
        "summary": f"Summary {ref}",
        "sentiment": sentiment,
        "is_rumor": False,
        "published_at": "2026-10-30T20:45:00+00:00",
        "items": [{"outlet": outlet, "title": f"Title {ref}", "url": f"https://example.com/{ref}"}],
        **more,
    }


STORIES = [
    story(10, "news", "Reuters", "bullish"),
    story(
        11,
        "video",
        "Sample Markets",
        "bullish",
        points=[
            {"text": "First point.", "time": "1:15", "url": "https://youtu.be/x?t=75"},
            {"text": "Second point.", "time": None, "url": "https://youtu.be/x"},
        ],
    ),
    story(12, "post", "@ExampleAnalyst", "bearish", is_rumor=True),
    story(13, "blog", "Sample Tech Blog", "neutral"),
    story(14, "post", "@SampleInvestor", None, summary=None),
]


def test_voices_are_the_videos_and_x_posts_with_their_source_numbers():
    result = voices(STORIES)
    entries = result["entries"]
    assert [(e["number"], e["label"], e["who"]) for e in entries] == [
        (2, "YouTube", "Sample Markets"),
        (3, "X", "@ExampleAnalyst"),
        (5, "X", "@SampleInvestor"),
    ]
    assert entries[0]["text"] == "Summary 11" and entries[0]["sentiment"] == "bullish"
    assert entries[0]["point"] == {
        "text": "First point.",
        "time": "1:15",
        "url": "https://youtu.be/x?t=75",
    }
    assert entries[0]["day"] == "Oct 30"
    assert entries[1]["unconfirmed"] is True and entries[1]["point"] is None
    assert entries[2]["text"] == "Title 14"  # no summary yet: the title stands in
    assert result["counts"] == {"bullish": 1, "neutral": 0, "bearish": 1}
    assert result["rated"] == 2


def test_no_voices_without_videos_or_posts():
    assert voices([STORIES[0], STORIES[3]]) == {
        "entries": [],
        "counts": {"bullish": 0, "neutral": 0, "bearish": 0},
        "rated": 0,
    }
    assert voices([])["entries"] == [] and voices(None)["entries"] == []


# --- on the page -------------------------------------------------------------------------

HEADING = "<h3>Voices: X and YouTube</h3>"


def test_default_report_has_no_voices_section(client):
    assert HEADING not in client.get(generate(client, ticker="AAPL")).text


def test_video_takes_are_shown_between_rumors_and_ownership(client, report_services, fake_fetch):
    with_blog_and_channel(report_services, fake_fetch)
    html = client.get(generate(client, ticker="AAPL")).text
    order = [
        html.index("<h3>Rumors &amp; speculation</h3>"),
        html.index(HEADING),
        html.index("<h3>Institutional &amp; insider activity</h3>"),
    ]
    assert order == sorted(order)
    section = html.split(HEADING)[1].split("</section>")[0]
    assert "Opinions from the channels and accounts you trust, not confirmed facts." in section
    entries = re.findall(r'<li class="voice">.*?</li>', section, flags=re.S)
    assert entries
    for entry in entries:
        assert '<span class="badge plain">YouTube</span>' in entry
        assert "<strong>Sample Markets</strong>" in entry
        anchor = re.search(r'<a class="cite" href="#(r\d+-s\d+)"', entry).group(1)
        assert f'<li id="{anchor}">' in html  # links to its numbered source (F4.2)
    assert "Sample Tech Blog" not in section  # blogs stay with the other sources
    assert 'title="Watch from 1:15">1:15</a> First point of:' in section


def test_x_posts_are_shown_as_voices(
    engine, fake_market_data, app_config, report_services, report_runner, fake_fred, fomc, fake_x
):
    app = create_app(
        engine=engine,
        market_data=fake_market_data,
        app_config=app_config,
        report_services=with_x(report_services, engine, fake_x),
        report_runner=report_runner,
        fred=fake_fred,
        fomc=fomc,
    )
    with TestClient(app) as client:
        html = client.get(generate(client, ticker="AAPL")).text
    section = html.split(HEADING)[1].split("</section>")[0]
    assert '<span class="badge plain">X</span>' in section
    assert "@ExampleAnalyst" in section
    tally = " ".join(
        re.search(r'<p class="voice-tally[^"]*">(.*?)</p>', section, flags=re.S).group(1).split()
    )
    assert re.fullmatch(r"\d+ voices?: \d+ bullish · \d+ neutral · \d+ bearish.*", tally)
