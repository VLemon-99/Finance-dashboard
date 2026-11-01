"""Ticker symbol rules, shared by services and data adapters."""

import re

# US tickers: 1-6 letters/digits starting with a letter, optionally a share class
# like BRK-B or BRK.B. Anything else is rejected before we call any outside service.
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}(?:-[A-Z]{1,2})?$")


class InvalidTicker(ValueError):
    pass


def normalize_ticker(raw: str) -> str:
    """'  brk.b ' -> 'BRK-B'. Raises InvalidTicker for anything that isn't ticker-shaped."""
    symbol = (raw or "").strip().upper().replace(".", "-")
    if not _TICKER_RE.fullmatch(symbol):
        raise InvalidTicker(raw)
    return symbol
