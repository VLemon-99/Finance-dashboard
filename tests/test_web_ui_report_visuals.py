"""IMP-18: valuation and sentiment shown as pictures (SPEC F4.6, F4.7).

Positions on the bars are calculated in Python (`app/web/report_view.py`) from figures the
stored report already holds; every number stays on the page as text.
"""

import re
from pathlib import Path

import pytest

from app.web.report_view import (
    history_bars,
    position,
    sentiment_bar,
    target_bar,
    vs_class,
)
from tests.test_web_reports import generate

CSS = Path("app/web/static/style.css").read_text()


def section(html, name):
    return html.split(f"<h3>{name}</h3>")[1].split("</section>")[0]


# --- the arithmetic ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "low", "high", "expected"),
    [
        (230, 180, 260, 62.5),
        (200, 180, 260, 25.0),
        (180, 180, 260, 0.0),
        (260, 180, 260, 100.0),
        (150, 180, 260, 0.0),  # clamped
        (300, 180, 260, 100.0),  # clamped
        (None, 180, 260, None),
        (200, None, 260, None),
        (200, 180, None, None),
        (200, 180, 180, None),  # no width
        (200, 260, 180, None),  # upside down
    ],
)
def test_position_between_low_and_high(value, low, high, expected):
    assert position(value, low, high) == expected


@pytest.mark.parametrize(
    ("fraction", "expected"),
    [
        (0.2, "premium"),
        (-0.25, "discount"),
        (0.004, ""),
        (-0.004, ""),
        (0.006, "premium"),
        (None, ""),
    ],
)
def test_vs_class_follows_the_shown_rounding(fraction, expected):
    assert vs_class(fraction) == expected


def test_target_bar_positions_and_price():
    bar = target_bar({"low": 180.0, "mean": 230.0, "high": 260.0, "mean_vs": 0.15})
    assert bar["mean_position"] == 62.5
    assert bar["price"] == pytest.approx(200.0)  # 230 / 1.15, the price the gap was made from
    assert bar["price_position"] == pytest.approx(25.0)
    assert bar["price_outside"] is None


def test_target_bar_price_outside_the_range_is_clamped_and_named():
    below = target_bar({"low": 180.0, "mean": 230.0, "high": 260.0, "mean_vs": 1.3})  # price 100
    assert (below["price_position"], below["price_outside"]) == (0.0, "below")
    above = target_bar({"low": 180.0, "mean": 230.0, "high": 260.0, "mean_vs": -0.5})  # price 460
    assert (above["price_position"], above["price_outside"]) == (100.0, "above")


def test_target_bar_needs_low_and_high():
    assert target_bar(None) is None
    assert target_bar({"low": None, "mean": 230.0, "high": 260.0, "mean_vs": 0.15}) is None
    assert target_bar({"low": 180.0, "mean": 230.0, "high": None, "mean_vs": 0.15}) is None
    assert target_bar({"low": 200.0, "mean": 200.0, "high": 200.0, "mean_vs": 0.0}) is None
    no_price = target_bar({"low": 180.0, "mean": 230.0, "high": 260.0, "mean_vs": None})
    assert no_price["price"] is None and no_price["price_position"] is None
    assert no_price["mean_position"] == 62.5


def test_history_bars_place_now_between_the_low_and_high_years():
    valuation = {
        "rows": [
            {"label": "P/E", "now": 30.0, "history": {"average": 25.0, "low": 20.0, "high": 40.0}},
            {"label": "Forward P/E", "now": 27.0, "history": None},
            {
                "label": "EV/EBITDA",
                "now": 50.0,
                "history": {"average": 17.0, "low": 12.0, "high": 22.0},
            },
            {"label": "P/S", "now": 2.0, "history": {"average": 5.5, "low": 4.0, "high": 8.0}},
            {"label": "Flat", "now": 3.0, "history": {"average": 3.0, "low": 3.0, "high": 3.0}},
            {"label": "Loss", "now": None, "history": {"average": 3.0, "low": 1.0, "high": 5.0}},
        ]
    }
    bars = history_bars(valuation)
    assert [b["label"] for b in bars] == ["P/E", "EV/EBITDA", "P/S"]
    assert (bars[0]["position"], bars[0]["outside"]) == (50.0, None)
    assert (bars[0]["low"], bars[0]["high"], bars[0]["now"]) == (20.0, 40.0, 30.0)
    assert (bars[1]["position"], bars[1]["outside"]) == (100.0, "above")
    assert (bars[2]["position"], bars[2]["outside"]) == (0.0, "below")
    assert history_bars(None) == [] and history_bars({}) == []


def test_sentiment_bar_shares_and_marker():
    bar = sentiment_bar({"score": 25, "counts": {"bullish": 2, "neutral": 6, "bearish": 0}})
    assert (bar["bullish"], bar["neutral"], bar["bearish"]) == (25.0, 75.0, 0.0)
    assert bar["marker"] == 62.5
    worst = sentiment_bar({"score": -100, "counts": {"bullish": 0, "neutral": 0, "bearish": 3}})
    assert (worst["bearish"], worst["marker"]) == (100.0, 0.0)
    thirds = sentiment_bar({"score": 0, "counts": {"bullish": 1, "neutral": 1, "bearish": 1}})
    assert thirds["bullish"] == pytest.approx(33.3333, abs=0.001)
    assert thirds["marker"] == 50.0


def test_sentiment_bar_without_stories_or_score():
    assert (
        sentiment_bar({"score": None, "counts": {"bullish": 0, "neutral": 0, "bearish": 0}}) is None
    )
    assert sentiment_bar({}) is None and sentiment_bar(None) is None
    unscored = sentiment_bar({"score": None, "counts": {"bullish": 1, "neutral": 0, "bearish": 0}})
    assert unscored["marker"] is None and unscored["bullish"] == 100.0


# --- on the page -------------------------------------------------------------------------


def test_valuation_table_marks_premium_and_discount(client):
    html = section(client.get(generate(client, ticker="AAPL")).text, "Valuation snapshot")
    pe_row = html.split('<th scope="row">P/E</th>')[1].split("</tr>")[0]
    assert '<span class="vs premium">+20%</span>' in pe_row  # vs own history
    assert '<span class="vs premium">+100%</span>' in pe_row  # vs peers
    assert '<span class="vs discount">−25%</span>' in pe_row  # vs sector
    assert "Premium = a higher multiple than the benchmark" in html
    # The analyst targets keep their plain spans.
    targets = html.split("Analyst targets")[1].split("</p>")[0]
    assert '<span class="vs">−10%</span>' in targets and "premium" not in targets


def test_premium_and_discount_chips_use_neutral_colours():
    for name in ("premium", "discount"):
        rule = re.search(rf"\.vs\.{name}\s*\{{([^}}]*)\}}", CSS)
        assert rule, name
        assert "--up" not in rule.group(1) and "--down" not in rule.group(1)


def test_analyst_target_range_bar(client):
    html = section(client.get(generate(client, ticker="AAPL")).text, "Valuation snapshot")
    bar = html.split('class="range target-range"')[1].split("</div>")[0]
    assert 'class="range target-range" role="img" aria-label="' in html
    assert 'class="range-mark mean" style="left: 62.5%"' in bar
    assert 'class="range-mark price" style="left: 25.0%"' in bar
    label = re.search(r'target-range" role="img" aria-label="([^"]+)"', html)
    assert "$180.00" in label.group(1) and "$260.00" in label.group(1)
    assert "price $200.00" in label.group(1)
    # The numbers are also visible as text.
    legend = html.split('class="range-legend"')[1].split("</p>")[0]
    assert "$200.00" in legend and "$230.00" in legend


def test_own_history_range_bars(client):
    html = section(client.get(generate(client, ticker="AAPL")).text, "Valuation snapshot")
    block = html.split('class="history-ranges"')[1].split("</dl>")[0]
    assert block.count('class="range history-range"') >= 1
    first = block.split("</dd>")[0]
    assert "P/E" in first
    assert re.search(r'class="range-mark now" style="left: \d+\.\d%"', first)
    assert re.search(r'class="range-end">\d+\.\d<', first)  # low and high as text


def test_sentiment_bars_and_existing_lines(client):
    html = section(client.get(generate(client, ticker="AAPL")).text, "Sentiment")
    assert 'class="scale-mark" style="left: 62.5%"' in html
    assert 'class="mix-part bullish" style="width: 25.0%"' in html
    assert 'class="mix-part neutral" style="width: 75.0%"' in html
    assert 'class="mix-part bearish"' not in html  # no empty slice
    assert 'aria-label="2 bullish, 6 neutral, 0 bearish"' in html
    assert "2 bullish · 6 neutral · 0 bearish" in html
    assert "on a scale from −100 (all bearish) to +100 (all bullish)" in html
    assert "Weights used:" in html


def test_no_bars_when_ai_ratings_are_off(client, report_services):
    report_services.llm = None
    report_services.llm_problem = "AI summaries are off: set GEMINI_API_KEY in the .env file."
    response = client.get(generate(client, ticker="AAPL"))
    assert response.status_code == 200
    html = section(response.text, "Sentiment")
    assert "mix-part" not in html and "scale-mark" not in html


def test_bars_have_text_alternatives(client):
    html = client.get(generate(client, ticker="AAPL")).text
    assert 'role="img" aria-label="P/E now 30.0; past years ' in html
    assert 'role="img" aria-label="Score +25 on a scale from −100 to +100"' in html
    for bar in re.findall(r'<(?:div|span) class="(?:range [a-z-]+|scale|mix)"[^>]*>', html):
        assert 'role="img" aria-label="' in bar, bar
    assert len(re.findall(r'class="(?:range [a-z-]+|scale|mix)"', html)) >= 4
