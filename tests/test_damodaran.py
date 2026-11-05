"""SPEC F4.7: Damodaran's industry averages, read from hand-made samples of his pages."""

from datetime import timedelta

import pytest

from app.config import DamodaranConfig
from app.providers.damodaran import (
    COLUMNS,
    DamodaranClient,
    DamodaranError,
    parse_page,
    table_grid,
)
from app.sources.base import SourceError
from tests.conftest import FIXTURES

PAGES = FIXTURES / "damodaran"


class PageFetch:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.down: set[str] = set()

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        name = url.rsplit("/", 1)[-1]
        if name in self.down:
            raise SourceError("The site didn't answer in time.")
        return (PAGES / name).read_bytes()


def test_pe_page():
    values, updated = parse_page((PAGES / "pedata.html").read_bytes(), COLUMNS["pe"])
    assert updated == "January 2026"
    assert values["Retail (Online)"] == {"trailing_pe": 42.0, "forward_pe": 33.0}
    assert values["Software (System & Application)"]["trailing_pe"] == 45.3
    assert "Total Market" in values


def test_ev_ebitda_page_takes_the_positive_ebitda_column_under_merged_headers():
    values, _ = parse_page((PAGES / "vebitda.html").read_bytes(), COLUMNS["ev_ebitda"])
    assert values["Retail (Online)"] == {"ev_ebitda": 21.4}  # not EV/EBITDAR&D, not "All firms"


def test_price_to_sales_page():
    values, _ = parse_page((PAGES / "psdata.html").read_bytes(), COLUMNS["price_to_sales"])
    assert values["Advertising"] == {"price_to_sales": 1.2}


def test_unreadable_page():
    with pytest.raises(DamodaranError):
        parse_page(b"<html><p>Moved</p></html>", COLUMNS["pe"])
    with pytest.raises(DamodaranError):
        parse_page(b"<table><tr><td>Industry Name</td><td>Other</td></tr></table>", COLUMNS["pe"])


def test_table_grid_fills_merged_cells():
    grid = table_grid(
        "<table><tr><td rowspan=2>A</td><td colspan=2>B</td></tr>"
        "<tr><td>c</td><td>d</td></tr></table>"
    )
    assert grid == [["A", "B", "B"], ["A", "c", "d"]]


def test_client_merges_the_three_pages_and_reuses_them_for_a_day(monkeypatch):
    fetch = PageFetch()
    client = DamodaranClient(fetch, DamodaranConfig())
    averages = client.averages()
    assert averages.updated == "January 2026" and averages.problems == ()
    name, m = averages.find("Retail (Online)")
    assert (m.trailing_pe, m.forward_pe, m.ev_ebitda, m.price_to_sales) == (42.0, 33.0, 21.4, 2.8)
    client.averages()
    assert len(fetch.calls) == 3  # downloaded once

    import app.providers.damodaran as module

    later = module.utc_now() + timedelta(hours=25)
    monkeypatch.setattr(module, "utc_now", lambda: later)
    client.averages()
    assert len(fetch.calls) == 6


def test_find_forgives_punctuation_and_small_spelling_changes():
    averages = DamodaranClient(PageFetch(), DamodaranConfig()).averages()
    assert (
        averages.find("software (system and application)")[0] == "Software (System & Application)"
    )
    # His page spells it "Heathcare"; the corrected spelling still finds it.
    assert averages.find("Healthcare Information and Technology")[0] == (
        "Heathcare Information and Technology"
    )
    assert averages.find("Underwater Basket Weaving") is None


def test_one_page_down_loses_only_its_columns():
    fetch = PageFetch()
    fetch.down.add("vebitda.html")
    averages = DamodaranClient(fetch, DamodaranConfig()).averages()
    _, m = averages.find("Retail (Online)")
    assert m.ev_ebitda is None and m.trailing_pe == 42.0
    assert len(averages.problems) == 1 and "ev ebitda" in averages.problems[0]


def test_all_pages_down():
    fetch = PageFetch()
    fetch.down |= {"pedata.html", "vebitda.html", "psdata.html"}
    with pytest.raises(DamodaranError, match="didn't answer"):
        DamodaranClient(fetch, DamodaranConfig()).averages()
