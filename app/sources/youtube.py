"""YouTube videos from the trusted channels in config/sources.yaml (SPEC F4.5).

New videos are found through each channel's free feed (no API key; see DECISIONS
2026-11-01). A video counts for a ticker when its title or description mentions the ticker
or company (mentions.py) and it's from the last `lookback_days` days.

This file only finds videos. Watching them (the AI summarizes the video itself, once per
video) happens later in the report run: see app/services/videos.py.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from app.config import YouTubeChannel, YouTubeConfig
from app.sources.base import Fetch, SourceItem
from app.sources.feeds import CachedFeed
from app.sources.mentions import company_names, mentions
from app.sources.text import html_to_text, struct_time_to_utc
from app.timeutils import utc_now

CHANNEL_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Longest description kept (characters).
MAX_DESCRIPTION_CHARS = 5_000


def watch_url(video_id: str) -> str:
    """The one address we use for a video, so the same video is always recognised."""
    if not _VIDEO_ID.match(video_id):
        raise ValueError(f"Not a YouTube video id: {video_id!r}")
    return WATCH_URL.format(video_id=video_id)


def at_time(url: str, seconds: int | None) -> str:
    """The video's address, starting at `seconds` (SPEC F4.5: "linked with a timestamp")."""
    return f"{url}&t={seconds}s" if seconds else url


class YouTubeSource:
    name = "youtube"
    source_type = "youtube"

    def __init__(
        self,
        fetch: Fetch,
        channel: YouTubeChannel,
        config: YouTubeConfig,
        also_known_as: Mapping[str, Sequence[str]],
    ) -> None:
        self.channel = channel
        self.label = f"YouTube: {channel.name}"
        self._config = config
        self._also_known_as = also_known_as
        self._feed = CachedFeed(
            fetch, CHANNEL_FEED_URL.format(channel_id=channel.channel_id), self.label
        )

    def fetch(self, ticker: str, company: str | None, since: datetime) -> list[SourceItem]:
        since = max(since, utc_now() - timedelta(days=self._config.lookback_days))
        names = company_names(company, self._also_known_as.get(ticker, ()))
        items = []
        for entry in self._feed.entries():
            video_id = entry.get("yt_videoid", "")
            published = struct_time_to_utc(entry.get("published_parsed"))
            if not _VIDEO_ID.match(video_id) or published is None or published < since:
                continue
            title = html_to_text(entry.get("title", ""))
            description = html_to_text(entry.get("summary", ""))[:MAX_DESCRIPTION_CHARS]
            if not mentions(f"{title}\n{description}", ticker, names):
                continue
            items.append(
                SourceItem(
                    source=self.name,
                    source_type="youtube",
                    ticker=ticker,
                    outlet=self.channel.name,
                    title=title or "(untitled video)",
                    url=watch_url(video_id),
                    published_at=published,
                    # Until the video is watched, the AI would only have this to go on.
                    text=f"{title}\n\n{description}",
                    data={"video_id": video_id, "description": description},
                )
            )
        return items

    def enrich(self, item: SourceItem) -> SourceItem:
        return item
