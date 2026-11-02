"""The AI interface. Services talk to this, never to Gemini directly (see gemini.py).

Privacy rule (SPEC F4.13): only public content goes to the AI. That's enforced by shape:
the methods below accept only these small public-content records (a ticker, a company
name, and articles/filings), never free text, watchlists, positions or trades.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

Sentiment = Literal["bullish", "neutral", "bearish"]
SENTIMENTS: tuple[Sentiment, ...] = ("bullish", "neutral", "bearish")


@dataclass(frozen=True)
class PublicItem:
    """One public article or filing, as the AI sees it."""

    ref: int  # our stored item id; the AI must answer with it
    kind: Literal["news", "filing"]
    outlet: str
    title: str
    published: str  # shown in US Eastern, e.g. "Oct 30, 2026 4:30 PM ET"
    text: str


@dataclass(frozen=True)
class ItemSummary:
    ref: int
    summary: str
    sentiment: Sentiment
    reason: str  # one line
    is_rumor: bool  # unconfirmed report or speculation


@dataclass(frozen=True)
class PublicStory:
    """One story (possibly reported by several outlets), already summarized."""

    ref: int  # the story id
    kind: Literal["news", "filing"]
    title: str
    outlets: tuple[str, ...]
    published: str
    summary: str
    is_rumor: bool


@dataclass(frozen=True)
class Claim:
    text: str
    refs: tuple[int, ...]  # story ids this statement is based on


@dataclass(frozen=True)
class ReportDraft:
    developments: tuple[Claim, ...]
    rumors: tuple[Claim, ...]


class AIError(Exception):
    """The AI service failed: not set up, down, timed out, over its limit, or answered oddly.

    The message is shown in the report's "missing sources" list, so keep it plain.
    """


class LLM(Protocol):
    model_name: str

    def summarize_items(
        self, ticker: str, company: str | None, items: list[PublicItem]
    ) -> list[ItemSummary]:
        """A summary and sentiment rating per item. Items it skipped are simply absent."""
        ...

    def write_report(
        self,
        ticker: str,
        company: str | None,
        stories: list[PublicStory],
        max_developments: int,
        max_rumors: int,
    ) -> ReportDraft:
        """The 'Major developments' and 'Rumors & speculation' statements, each citing stories."""
        ...
