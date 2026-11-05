"""One blog's or YouTube channel's feed, downloaded once and reused for a few minutes.

A report run asks every source about every ticker, but a blog or channel feed is the same
for all of them, so it's fetched once per run instead of 25 times. A failure is reused too,
so a site that's down costs one timeout per run, not one per ticker.

A feed can be given `retries`: after a failed download it waits `retry_wait` seconds and
tries again. Used for YouTube, whose channel feeds often fail for a moment with a 404 or 500
(seen 2026-11-04, and widely reported since late 2025) and then work again.
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
        retries: int = 0,
        retry_wait: float = 0.0,
        sleep: Callable[[float], None] | None = None,  # default: pause() below
    ) -> None:
        self._fetch = fetch
        self.url = url
        self._label = label
        self._clock = clock
        self._fetched_at: float | None = None
        self._entries: list = []
        self._error: SourceError | None = None
        self._retries = retries
        self._retry_wait = retry_wait
        self._sleep = sleep

    def entries(self) -> list:
        """The feed's entries. Raises SourceError (the same one again while it's reused)."""
        now = self._clock()
        if self._fetched_at is None or now - self._fetched_at >= REUSE_SECONDS:
            self._fetched_at = now
            self._entries, self._error = [], None
            try:
                self._entries = _parse(self._download(), self._label)
            except SourceError as exc:
                self._error = exc
        if self._error is not None:
            raise self._error
        return self._entries

    def _download(self) -> bytes:
        """The feed's bytes, trying again after a short wait if the site answered with an
        error (the last error is raised if every try fails)."""
        for attempt in range(self._retries + 1):
            try:
                return self._fetch(self.url)
            except SourceError:
                if attempt == self._retries:
                    raise
                (self._sleep or pause)(self._retry_wait)
        raise AssertionError("unreachable")


def pause(seconds: float) -> None:
    """Wait before trying a feed again (tests replace this so they don't wait)."""
    time.sleep(seconds)


def _parse(body: bytes, label: str) -> list:
    feed = feedparser.parse(body)
    if feed.get("bozo") and not feed.entries:
        raise SourceError(f"{label} sent something that isn't a readable feed.")
    return feed.entries
