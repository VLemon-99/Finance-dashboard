"""Small text helpers shared by the source plug-ins."""

import calendar
import re
import time
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser

_SKIP_TAGS = {"script", "style", "head", "title", "noscript"}
_BLOCK_TAGS = {"p", "div", "br", "tr", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Readable plain text from HTML: tags removed, blank runs squeezed."""
    if "<" not in html:
        return _squeeze(unescape(html))
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return _squeeze("".join(parser.parts))


def _squeeze(text: str) -> str:
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def struct_time_to_utc(value: time.struct_time | None) -> datetime | None:
    """feedparser gives dates as UTC `struct_time`s."""
    if value is None:
        return None
    return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)
