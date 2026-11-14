"""The AI interface. Services talk to this, never to Gemini directly (see gemini.py).

Privacy rule (SPEC F4.13): only public content goes to the AI. That's enforced by shape:
the methods below accept only these small public-content records (a ticker, a company
name, articles/filings/blog posts/X posts, and public YouTube video links), never free text,
watchlists, positions or trades.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

Sentiment = Literal["bullish", "neutral", "bearish"]
SENTIMENTS: tuple[Sentiment, ...] = ("bullish", "neutral", "bearish")
ItemKind = Literal["news", "filing", "blog", "video", "post"]  # post: on X (Twitter)


@dataclass(frozen=True)
class PublicItem:
    """One public article, filing, blog post or (already watched) video, as the AI sees it."""

    ref: int  # our stored item id; the AI must answer with it
    kind: ItemKind
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
    kind: ItemKind
    title: str
    outlets: tuple[str, ...]
    published: str
    summary: str
    is_rumor: bool


@dataclass(frozen=True)
class PublicVideo:
    """A public YouTube video for the AI to watch (SPEC F4.5). The AI fetches it itself."""

    url: str  # https://www.youtube.com/watch?v=...
    channel: str
    title: str
    published: str
    max_seconds: int  # watch at most this much, from the start


@dataclass(frozen=True)
class VideoPoint:
    text: str
    seconds: int | None  # where in the video this is said, if known


@dataclass(frozen=True)
class Claim:
    text: str
    refs: tuple[int, ...]  # story ids this statement is based on


@dataclass(frozen=True)
class ReportDraft:
    developments: tuple[Claim, ...]
    rumors: tuple[Claim, ...]
    # The synthesis (owner 2026-11-14): the overall picture, then the points for and against.
    bottom_line: tuple[Claim, ...] = ()
    bull_case: tuple[Claim, ...] = ()
    bear_case: tuple[Claim, ...] = ()


class AIError(Exception):
    """The AI service failed: not set up, down, timed out, over its limit, or answered oddly.

    The message is shown in the report's "missing sources" list, so keep it plain.
    """


class VideoUnavailable(AIError):
    """This one video can't be watched (private, removed, too long...). Others still can."""


class LLM(Protocol):
    model_name: str

    def summarize_items(
        self, ticker: str, company: str | None, items: list[PublicItem]
    ) -> list[ItemSummary]:
        """A summary and sentiment rating per item. Items it skipped are simply absent."""
        ...

    def watch_video(self, video: PublicVideo) -> tuple[VideoPoint, ...]:
        """3-5 points summarizing the video itself, with times. Raises VideoUnavailable for
        this video only, AIError when the service itself fails."""
        ...

    def write_report(
        self,
        ticker: str,
        company: str | None,
        stories: list[PublicStory],
        max_developments: int,
        max_rumors: int,
    ) -> ReportDraft:
        """The report's written parts, each statement citing stories: the bottom line, the
        major developments, rumors & speculation, and the bull and bear cases."""
        ...
