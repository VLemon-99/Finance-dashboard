"""The ticker's overall sentiment score (SPEC F4.6). Calculated here in code, not by the AI.

Each rated story counts +1 (bullish), 0 (neutral) or -1 (bearish), multiplied by its source
type's weight from config/settings.yaml (news and filings 1.0, blogs 0.8, ...):

    score = 100 x (sum of weight x rating) / (sum of weights)

rounded to a whole number, so it runs from -100 (all bearish) to +100 (all bullish).
Neutral stories count in the total weight, so they pull the score toward 0.
With nothing rated there is no score (None), rather than a misleading 0.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.ai.llm import SENTIMENTS

_VALUES = {"bullish": 1, "neutral": 0, "bearish": -1}


@dataclass(frozen=True)
class SentimentScore:
    score: int | None
    counts: dict[str, int]  # {"bullish": 3, "neutral": 1, "bearish": 0}


def sentiment_score(
    ratings: Iterable[tuple[str, str]], weights: Mapping[str, float]
) -> SentimentScore:
    """`ratings`: (source type, sentiment) per rated story. Unknown source types weigh 1.0."""
    counts = dict.fromkeys(SENTIMENTS, 0)
    weighted_sum = 0.0
    total_weight = 0.0
    for source_type, sentiment in ratings:
        if sentiment not in _VALUES:
            continue
        weight = weights.get(source_type, 1.0)
        counts[sentiment] += 1
        weighted_sum += weight * _VALUES[sentiment]
        total_weight += weight
    if total_weight <= 0:
        return SentimentScore(score=None, counts=counts)
    # round() on .5 goes to the even number; add a tiny nudge so +12.5 -> 13 and -12.5 -> -13.
    raw = 100 * weighted_sum / total_weight
    score = int(raw + 0.5) if raw >= 0 else -int(-raw + 0.5)
    return SentimentScore(score=score, counts=counts)
