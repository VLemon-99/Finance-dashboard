"""F7.10 (story F7-S21, security review L1): libraries stay past versions with known security
issues."""

from importlib.metadata import version


def _parts(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split(".")[:3] if p.isdigit())


def test_f7_10_soupsieve_is_past_cve_2026_126190():
    # Used by yfinance (through beautifulsoup4) to read Yahoo's pages; 3.x could be made very
    # slow by a specially built page. Fixed in 4.0.0.
    assert _parts(version("soupsieve")) >= (4, 0, 0)
