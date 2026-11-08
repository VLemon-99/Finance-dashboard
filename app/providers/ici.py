"""ICI (Investment Company Institute) weekly money market fund assets, for SPEC F6.1.

The only file that reads ICI's site. Free, no key. Every Thursday ICI publishes the total
held in US money market funds as of the day before, on its "Money Market Fund Assets" page,
in a sentence like:

    Total money market fund assets increased by $33.62 billion to $7.42 trillion for the
    week ended Wednesday, November 5, the Investment Company Institute reported today.

We read that sentence (the page's tables and download links have changed over the years;
the sentence hasn't). Only the latest week is on the page, so the app saves each week's
figure and the chart grows from the first run. The page address is in config/macro.yaml.
"""

import html
import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.sources.base import Fetch, SourceError

SOURCE_NAME = "ICI weekly money market fund assets"

_MONTHS = {
    name[:3].lower(): number
    for number, name in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}
_AMOUNT = r"\$\s?([\d,]+(?:\.\d+)?)\s*(trillion|billion)"
_SENTENCE = re.compile(
    r"total money market fund assets\s+(increased|decreased|rose|fell|were\s+unchanged)"
    rf"(?:\s+by\s+{_AMOUNT})?"
    rf"(?:\s+to|\s+at)\s+{_AMOUNT}"
    r"\s+for\s+the\s+[\w\s-]{0,30}?(?:week|period)\s+ended\s+"
    r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day,?\s+)?"
    r"([a-z]{3,9})\.?\s+(\d{1,2})(?:,\s*(\d{4}))?",
    re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


class IciError(Exception):
    """ICI's page couldn't be read. The message is shown on the macro page."""


@dataclass(frozen=True)
class WeeklyAssets:
    week_ended: date
    total_billions: float  # all money market funds, billions of US dollars
    change_billions: float | None  # change from the week before, if the page says


class IciClient:
    def __init__(self, fetch: Fetch, page_url: str) -> None:
        self._fetch = fetch
        self.page_url = page_url

    def latest(self, today: date) -> WeeklyAssets:
        """The latest week's total. Raises IciError."""
        try:
            body = self._fetch(self.page_url)
        except SourceError as exc:
            raise IciError(f"Couldn't read ICI's page: {exc}") from exc
        return weekly_assets_from_page(body, today)


def weekly_assets_from_page(body: bytes, today: date) -> WeeklyAssets:
    """The latest "Total money market fund assets ... for the week ended ..." sentence."""
    text = _SPACES.sub(" ", html.unescape(_TAGS.sub(" ", body.decode("utf-8", "replace"))))
    found = []
    for match in _SENTENCE.finditer(text):
        direction, change, change_unit, total, total_unit, month, day, year = match.groups()
        week_ended = _week_end(month, day, year, today)
        if week_ended is None:
            continue
        change_billions = None
        if change is not None:
            change_billions = _billions(change, change_unit)
            if direction.lower() in ("decreased", "fell"):
                change_billions = -change_billions
        elif "unchanged" in direction.lower():
            change_billions = 0.0
        found.append(WeeklyAssets(week_ended, _billions(total, total_unit), change_billions))
    if not found:
        raise IciError(
            "ICI's page has changed: the weekly total wasn't found. Check the page address in "
            "config/macro.yaml."
        )
    return max(found, key=lambda w: w.week_ended)


def _billions(amount: str, unit: str) -> float:
    value = float(amount.replace(",", ""))
    return value * 1000 if unit.lower() == "trillion" else value


def _week_end(month: str, day: str, year: str | None, today: date) -> date | None:
    number = _MONTHS.get(month[:3].lower())
    if number is None:
        return None
    try:
        if year:
            return date(int(year), number, int(day))
        # No year in the sentence: the most recent such date (a week ended in December,
        # read in early January, belongs to last year).
        guess = date(today.year, number, int(day))
        if guess > today + timedelta(days=7):
            guess = date(today.year - 1, number, int(day))
        return guess
    except ValueError:
        return None
