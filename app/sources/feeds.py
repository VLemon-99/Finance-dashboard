"""One blog's or YouTube channel's feed, downloaded once and reused for a few minutes.

A report run asks every source about every ticker, but a blog or channel feed is the same
for all of them, so it's fetched once per run instead of 25 times. A failure is reused too,
so a site that's down costs one timeout per run, not one per ticker.
"""

import time
from collections.abc import Callable

import feedparser

from app.sources.base import Fetch, SourceError

# How long a downloaded feed (or its failure) is reused.
REUSE_SECONDS = 10 * 60


class CachedFeed:
    def __init__(
        self,
        fetch: Fetch,
        url: str,
        label: str,
        clock: Callable[[], float] = time.monotonic,  # tests pass a fake clock
    ) -> None:
        self._fetch = fetch
        self.url = url
        self._label = label
        self._clock = clock
        self._fetched_at: float | None = None
        self._entries: list = []
        self._error: SourceError | None = None

    def entries(self) -> list:
        """The feed's entries. Raises SourceError (the same one again while it's reused)."""
        now = self._clock()
        if self._fetched_at is None or now - self._fetched_at >= REUSE_SECONDS:
            self._fetched_at = now
            self._entries, self._error = [], None
            try:
                self._entries = _parse(self._fetch(self.url), self._label)
            except SourceError as exc:
                self._error = exc
        if self._error is not None:
            raise self._error
        return self._entries


def _parse(body: bytes, label: str) -> list:
    feed = feedparser.parse(body)
    if feed.get("bozo") and not feed.entries:
        raise SourceError(f"{label} sent something that isn't a readable feed.")
    return feed.entries
