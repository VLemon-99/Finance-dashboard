"""Sentiment score (SPEC F4.6): known answers for the formula."""

import pytest

from app.services.sentiment import sentiment_score

WEIGHTS = {"filing": 1.0, "news": 1.0, "blog": 0.8, "youtube": 0.7, "x": 0.5, "reddit": 0.3}


@pytest.mark.parametrize(
    ("ratings", "score"),
    [
        ([("news", "bullish")], 100),
        ([("news", "bearish"), ("filing", "bearish")], -100),
        ([("news", "neutral")], 0),
        # (1 + 1 - 1 + 0) / 4 = 0.25
        (
            [("news", "bullish"), ("news", "bullish"), ("filing", "bearish"), ("news", "neutral")],
            25,
        ),
        # (1.0 - 0.3) / (1.0 + 0.3) = 0.538 -> 54
        ([("news", "bullish"), ("reddit", "bearish")], 54),
        # (0.8 - 0.5 + 0) / (0.8 + 0.5 + 0.7) = 0.15 -> 15
        ([("blog", "bullish"), ("x", "bearish"), ("youtube", "neutral")], 15),
        # 1 / 8 = 12.5 -> 13 (halves round away from zero); -12.5 -> -13
        ([("news", "bullish")] + [("news", "neutral")] * 7, 13),
        ([("news", "bearish")] + [("news", "neutral")] * 7, -13),
    ],
)
def test_known_answers(ratings, score):
    assert sentiment_score(ratings, WEIGHTS).score == score


def test_counts_by_rating():
    result = sentiment_score(
        [("news", "bullish"), ("news", "bullish"), ("filing", "bearish")], WEIGHTS
    )
    assert result.counts == {"bullish": 2, "neutral": 0, "bearish": 1}


def test_no_ratings_means_no_score():
    result = sentiment_score([], WEIGHTS)
    assert result.score is None
    assert result.counts == {"bullish": 0, "neutral": 0, "bearish": 0}


def test_unknown_ratings_are_ignored_and_unknown_types_weigh_one():
    assert sentiment_score([("news", "great"), ("podcast", "bearish")], WEIGHTS).score == -100
