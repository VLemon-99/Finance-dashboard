"""Downloading web pages and feeds for the source plug-ins.

One small class so every source gets the same timeout, size cap, error messages and
(for the SEC) a polite pace. Tests never use this: they pass a fake `Fetch` instead.
"""

import logging
import threading
import time
from urllib.parse import urlsplit

import httpx

from app.sources.base import SourceError

log = logging.getLogger(__name__)

# Nothing we read should be bigger than this. Stops a runaway download filling memory.
MAX_BYTES = 25_000_000


MAX_REDIRECTS = 5


def _refuse_non_https(request: httpx.Request) -> None:
    """A redirect to a plain-http address is refused before it's sent (the first address
    is checked in `__call__`)."""
    if request.url.scheme != "https":
        raise SourceError("Refused a redirect to a non-HTTPS address.")


class HttpFetcher:
    """Callable `Fetch`: `fetcher(url) -> bytes`, raising SourceError on any problem."""

    def __init__(
        self,
        user_agent: str,
        timeout_seconds: float = 15.0,
        min_interval_seconds: float = 0.0,
        transport: httpx.BaseTransport | None = None,  # tests only
    ) -> None:
        self._client = httpx.Client(
            transport=transport,
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout_seconds,
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            # Checked for every request, redirects included (security review L2, F7.10).
            event_hooks={"request": [_refuse_non_https]},
        )
        # The SEC asks for no more than 10 requests a second; we leave a gap between calls.
        self._min_interval = min_interval_seconds
        self._lock = threading.Lock()
        self._last_call = 0.0

    def __call__(self, url: str) -> bytes:
        if urlsplit(url).scheme != "https":
            raise SourceError("Refused a non-HTTPS address.")
        self._wait_turn()
        try:
            with self._client.stream("GET", url) as response:
                if response.status_code == 429:
                    raise SourceError("The site says we're asking too often (rate limit).")
                if response.status_code == 404:
                    raise SourceError("The site says this page doesn't exist (404).")
                if response.status_code >= 400:
                    raise SourceError(f"The site answered with an error ({response.status_code}).")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise SourceError("The download was unexpectedly large; skipped.")
                return bytes(body)
        except httpx.TimeoutException as exc:
            raise SourceError("The site didn't answer in time.") from exc
        except httpx.HTTPError as exc:
            log.warning("Download failed for %s: %s", url, exc)
            raise SourceError(f"Couldn't reach the site ({type(exc).__name__}).") from exc

    def _wait_turn(self) -> None:
        if not self._min_interval:
            return
        with self._lock:
            gap = self._min_interval - (time.monotonic() - self._last_call)
            if gap > 0:
                time.sleep(gap)
            self._last_call = time.monotonic()

    def close(self) -> None:
        self._client.close()
