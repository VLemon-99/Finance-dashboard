"""Blog posts from the trusted blogs in config/sources.yaml (SPEC F4.3, F4.4).

Each blog is its own source, so one blog being down is listed by name and doesn't affect the
others (SPEC F4.11). A post is used when its title or summary mentions the ticker or company
(see mentions.py). The post's text from the feed (the full post, if the feed carries it,
otherwise its summary) is what the AI reads; we don't download the blog's pages.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from app.config import BlogFeed
from app.sources.base import Fetch, SourceItem
from app.sources.feeds import CachedFeed
from app.sources.mentions import company_names, mentions
from app.sources.text import html_to_text, struct_time_to_utc

# Longest post text kept (characters). Long enough for a full post; keeps AI calls small.
MAX_POST_CHARS = 20_000


class BlogSource:
    name = "blog"
    source_type = "blog"

    def __init__(
        self, fetch: Fetch, blog: BlogFeed, also_known_as: Mapping[str, Sequence[str]]
    ) -> None:
        self.blog = blog
        self.label = f"Blog: {blog.name}"
        self._feed = CachedFeed(fetch, blog.feed, self.label)
        self._also_known_as = also_known_as

    def fetch(self, ticker: str, company: str | None, since: datetime) -> list[SourceItem]:
        names = company_names(company, self._also_known_as.get(ticker, ()))
        items = []
        for entry in self._feed.entries():
            published = struct_time_to_utc(
                entry.get("published_parsed") or entry.get("updated_parsed")
            )
            link = entry.get("link", "")
            if (
                published is None
                or published < since
                or not link.startswith(("https://", "http://"))
            ):
                continue
            title = html_to_text(entry.get("title", ""))
            summary = html_to_text(entry.get("summary", ""))
            if not mentions(f"{title}\n{summary}", ticker, names):
                continue
            content = entry.get("content") or []
            body = html_to_text(content[0].get("value", "")) if content else ""
            text = body if len(body) > len(summary) else summary
            items.append(
                SourceItem(
                    source=self.name,
                    source_type="blog",
                    ticker=ticker,
                    outlet=self.blog.name,
                    title=title or "(untitled post)",
                    url=link,
                    published_at=published,
                    text=f"{title}\n\n{text}"[:MAX_POST_CHARS],
                )
            )
        return items

    def enrich(self, item: SourceItem) -> SourceItem:
        return item
