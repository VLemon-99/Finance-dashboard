"""Professor Aswath Damodaran's US industry averages (NYU Stern), for SPEC F4.7.

This is the only file that reads his data pages. Free, no key, updated once a year (each
January). Three pages, each one table with a row per industry:
- pedata.html    -> trailing P/E and forward P/E
- vebitda.html   -> EV/EBITDA (the first EV/EBITDA column: firms with positive EBITDA)
- psdata.html    -> price/sales

The pages are Excel exported to HTML, so the reader is forgiving: it rebuilds the table
grid (merged cells included), finds the header row by its "Industry Name" cell, and picks
each column by its header text, wherever it is. A page that fails or changes shape only
loses its own columns; the problem is reported, never fatal.
"""

import difflib
import logging
import re
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from html.parser import HTMLParser

from app.config import DamodaranConfig
from app.providers.market_data import Multiples
from app.sources.base import Fetch, SourceError
from app.timeutils import utc_now

log = logging.getLogger(__name__)

# Normalized header text -> our field. A header matches when its (merged) text ends with
# one of these, e.g. "Only positive EBITDA firms / EV/EBITDA" -> "...evebitda".
COLUMNS = {
    "pe": {"trailing_pe": ("trailingpe",), "forward_pe": ("forwardpe",)},
    "ev_ebitda": {"ev_ebitda": ("evebitda",)},
    "price_to_sales": {"price_to_sales": ("pricesales", "pricetosales")},
}
_MONTH_YEAR = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|"
    r"December)\s+(?:\d{1,2},\s*)?(20\d\d)\b"
)


class DamodaranError(Exception):
    """His pages couldn't be read. The message is shown in the report's missing list."""


@dataclass(frozen=True)
class IndustryAverages:
    updated: str | None  # e.g. "January 2026", from the pages' "Date updated" line
    industries: dict[str, Multiples]  # his industry name -> its averages
    problems: tuple[str, ...] = ()  # pages that couldn't be read

    def find(self, name: str) -> tuple[str, Multiples] | None:
        """Look an industry up by name, forgiving punctuation and small spelling changes."""
        keys = {_key(n): n for n in self.industries}
        wanted = _key(name)
        found = keys.get(wanted)
        if found is None:
            close = difflib.get_close_matches(wanted, list(keys), n=1, cutoff=0.85)
            found = keys[close[0]] if close else None
        return (found, self.industries[found]) if found else None


@dataclass
class DamodaranClient:
    fetch: Fetch
    config: DamodaranConfig
    max_age: timedelta = timedelta(hours=24)
    _cached: IndustryAverages | None = field(default=None, init=False)
    _cached_at: datetime | None = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def averages(self) -> IndustryAverages:
        """All industries' averages (downloaded at most once a day). Raises DamodaranError
        only if no page could be read."""
        with self._lock:
            fresh = self._cached_at is not None and utc_now() - self._cached_at < self.max_age
            if self._cached is not None and fresh:
                return self._cached
            averages = self._download()
            if not averages.problems:  # a partial answer is used, but asked for again next time
                self._cached, self._cached_at = averages, utc_now()
            return averages

    def _download(self) -> IndustryAverages:
        urls = {
            "pe": self.config.pe_url,
            "ev_ebitda": self.config.ev_ebitda_url,
            "price_to_sales": self.config.price_to_sales_url,
        }
        merged: dict[str, dict[str, float | None]] = {}
        updated, problems = None, []
        for page, url in urls.items():
            try:
                values, page_updated = parse_page(self.fetch(url), COLUMNS[page])
            except (SourceError, DamodaranError) as exc:
                log.warning("Damodaran %s page failed: %s", page, exc)
                problems.append(f"{page.replace('_', ' ')} page: {exc}")
                continue
            updated = updated or page_updated
            for name, fields in values.items():
                merged.setdefault(name, {}).update(fields)
        if not merged:
            raise DamodaranError("; ".join(problems) or "No industries found.")
        return IndustryAverages(
            updated=updated,
            industries={n: replace(Multiples(), **f) for n, f in merged.items()},
            problems=tuple(problems),
        )


# --- reading a page (pure functions, tested with hand-made samples) -------------------


def parse_page(
    body: bytes, wanted: dict[str, tuple[str, ...]]
) -> tuple[dict[str, dict[str, float | None]], str | None]:
    """One data page -> ({industry: {field: value}}, "Month YYYY" it was updated)."""
    html = body.decode("utf-8", errors="replace")
    grid = table_grid(html)
    header_at = next(
        (i for i, row in enumerate(grid) if row and _key(row[0]).startswith("industry")), None
    )
    if header_at is None:
        raise DamodaranError("the table's 'Industry Name' header wasn't found.")
    first_data = next(
        (i for i in range(header_at + 1, len(grid)) if _is_data_row(grid[i])), len(grid)
    )
    headers = [
        "".join(_key(grid[r][c]) for r in range(header_at, first_data) if c < len(grid[r]))
        for c in range(max(len(r) for r in grid[header_at:first_data]))
    ]
    columns = {}
    for name, endings in wanted.items():
        found = next((c for c, h in enumerate(headers) if h.endswith(endings)), None)
        if found is not None:
            columns[name] = found
    if not columns:
        raise DamodaranError("the expected columns weren't found.")
    values = {}
    for row in grid[first_data:]:
        if not _is_data_row(row):
            continue
        values[row[0].strip()] = {
            name: _number(row[c]) if c < len(row) else None for name, c in columns.items()
        }
    if not values:
        raise DamodaranError("the table had no industries.")
    month = _MONTH_YEAR.search(_text(html))
    return values, f"{month.group(1)} {month.group(2)}" if month else None


class _TableReader(HTMLParser):
    """Collects the first table's rows as (text, rowspan, colspan) cells."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, int, int]]] = []
        self._depth = 0
        self._done = False
        self._cell: list[str] | None = None
        self._spans = (1, 1)

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        if tag == "table":
            self._depth += 1
        elif tag == "tr" and self._depth == 1:
            self.rows.append([])
        elif tag in ("td", "th") and self._depth == 1 and self.rows:
            a = dict(attrs)
            self._cell = []
            self._spans = (_span(a.get("rowspan")), _span(a.get("colspan")))
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if self._done:
            return
        if tag in ("td", "th") and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            self.rows[-1].append((text, *self._spans))
            self._cell = None
        elif tag == "table":
            self._depth -= 1
            if self._depth == 0 and self.rows:
                self._done = True

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def table_grid(html: str) -> list[list[str]]:
    """The first table's cells as a grid: a merged cell's text fills every spot it covers."""
    reader = _TableReader()
    reader.feed(html)
    grid: list[list[str]] = []
    pending: dict[tuple[int, int], str] = {}  # (row, col) filled by a rowspan from above
    for r, cells in enumerate(reader.rows):
        row: list[str] = []
        c = 0
        cells = list(cells)
        while cells or any(k[0] == r and k[1] >= c for k in pending):
            if (r, c) in pending:
                row.append(pending.pop((r, c)))
                c += 1
                continue
            if not cells:
                c += 1
                row.append("")
                continue
            text, rowspan, colspan = cells.pop(0)
            for dc in range(colspan):
                row.append(text)
                for dr in range(1, rowspan):
                    pending[(r + dr, c + dc)] = text
            c += colspan
        grid.append(row)
    return grid


def _is_data_row(row: list[str]) -> bool:
    """An industry row: a name first, then at least one number."""
    return bool(row and row[0].strip()) and any(_number(v) is not None for v in row[1:])


def _number(text: str) -> float | None:
    cleaned = re.sub(r"[$,%\s]", "", text or "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value == value and abs(value) != float("inf") else None


def _span(value: str | None) -> int:
    try:
        return max(1, min(int(value or 1), 50))
    except ValueError:
        return 1


def _key(text: str) -> str:
    """Lowercase letters and digits only: 'EV/EBITDA' -> 'evebitda'."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _text(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())
